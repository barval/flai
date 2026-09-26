"""RLM (Recursive Language Models) deep-analysis module."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.utils import format_prompt
from modules.base import response_language_name

RLM_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "python",
            "description": "Execute Python over the corpus. Use context[filename] to access text.",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string", "description": "Python code to execute."}},
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "llm",
            "description": "Ask a sub-model a question about a specific text fragment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Search the web and fetch readable page content.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "final",
            "description": "Finish the analysis and return the final answer.",
            "parameters": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        },
    },
]


@dataclass
class RlmTraceStep:
    step: int
    tool: str
    args: str
    observation: str


@dataclass
class RlmResult:
    answer: str
    trace: list[RlmTraceStep] = field(default_factory=list)
    steps: int = 0
    error: str = ""


def _get_hardware() -> Any | None:
    """Return detected HardwareInfo, or None when unknown/unavailable.

    Kept as a module-level indirection so tests can patch it without
    instantiating the ResourceManager singleton.
    """
    try:
        from app.resource_manager import get_resource_manager

        return get_resource_manager().hardware
    except Exception:
        return None


# Step ladder keyed by VRAM tier (MB). Evaluated top-down; thresholds sit
# below the nominal card size because usable VRAM is always reported slightly
# lower (e.g. RTX 4060 Ti 16 GB reports 16311 MB). The CPU entry is a floor
# for GPU-less hosts and VRAM tiers too small for any LLM.
_STEP_LADDER: tuple[tuple[int, int], ...] = (
    (20480, 18),  # 24 GB tier
    (14336, 12),  # 16 GB tier (reference host: 16311 MB usable)
    (10240, 10),  # 12 GB tier
    (6144, 8),  # 8 GB tier
)
_STEP_LADDER_FLOOR = 6  # CPU-only / <8 GB — analysis must stay usable
_STEP_LADDER_MAX = 18


def _resource_step_budget(hw: Any | None) -> int:
    """Per-host step allowance from detected hardware (0 = no extra cap)."""
    if hw is None:
        return 0
    try:
        platform = getattr(hw, "platform", "cpu")
        vram = int(getattr(hw, "total_vram_mb", 0) or 0)
    except Exception:
        return 0
    if platform == "cpu" or vram <= 0:
        return _STEP_LADDER_FLOOR
    for threshold, steps in _STEP_LADDER:
        if vram >= threshold:
            return steps
    return _STEP_LADDER_FLOOR


# Per-step wall-clock costs in seconds: (base overhead, per-step cost).
# CPU generation is roughly 3x slower than GPU per actor turn.
_STEP_TIMEOUT_MODEL: dict[str, tuple[int, int]] = {
    "gpu": (120, 90),
    "cpu": (240, 300),
}

# Absolute floor for context-fitted observation truncation: below this the
# actor cannot see enough of a tool result to act on it.
_MIN_OBS_CHARS = 800


def _obs_trunc_for_context(steps: int, configured_trunc: int, boot_tokens: int) -> int:
    """Shrink the observation truncation so the whole trajectory fits the window.

    Steps are never cut for context reasons (the ladder decides those): when
    the window is too small, observations compress instead — down to
    _MIN_OBS_CHARS, never below. The caller must run inside an app context
    (reads RLM_MAX_STEPS ceiling and the reasoning model's context_length).
    """
    from app.model_config import get_model_config
    from app.utils import estimate_tokens

    cfg = get_model_config("reasoning") or {}
    context = cfg.get("context_length") or 0
    if not context:
        return configured_trunc
    available = int(context * 0.95) - boot_tokens - 1000
    if available <= 0:
        return _MIN_OBS_CHARS
    # tokens per observation char (e.g. ~0.5 for Russian, ~0.31 for English)
    tokens_per_char = estimate_tokens("а" * 4000, "reasoning", "ru") / 4000
    step_overhead_tokens = 300
    obs_budget = (available // max(1, steps)) - step_overhead_tokens
    obs_chars = int(obs_budget / tokens_per_char) if tokens_per_char > 0 else _MIN_OBS_CHARS
    return int(max(_MIN_OBS_CHARS, min(configured_trunc, obs_chars)))


class _RlmBroker:
    """Serves sandbox callbacks and direct tool calls."""

    def __init__(self, module: RlmModule, lang: str, sub_max_tokens: int, web_max_fetches: int) -> None:
        self.module = module
        self.lang = lang
        self.sub_max_tokens = sub_max_tokens
        self.web_max_fetches = web_max_fetches
        self.web_fetches = 0
        self.seen_queries: set[str] = set()

    def llm(self, prompt: str, text: str = "") -> str:
        return self.module.broker_llm(prompt, text)

    def web_fetch(self, query: str) -> str:
        return self.module.broker_web_fetch(query, self)


class RlmModule:
    def __init__(self, app: Any = None) -> None:
        self.app = app
        self.logger = logging.getLogger(__name__)

    def tool_definitions(self, lang: str = "ru") -> list[dict[str, Any]]:
        return RLM_TOOL_DEFINITIONS

    def build_system_prompt(self, lang: str = "ru", max_steps: int | None = None) -> str:
        from flask import current_app

        if max_steps is None:
            max_steps = current_app.config.get("RLM_MAX_STEPS", 12)
        return (
            format_prompt(
                "rlm.template",
                {"max_steps": max_steps, "response_language": response_language_name(lang)},
                lang=lang,
            )
            or ""
        )

    def _effective_max_steps(self, boot_texts: list[str], lang: str) -> int:
        """Combine the configured ceiling with the per-host resource ladder.

        The context window does NOT cut steps — observations compress instead
        (_obs_trunc_for_context). Only two caps apply here: RLM_MAX_STEPS
        (config ceiling, hard max 18) and the resource ladder (24 GB+→18,
        16 GB→12, 12 GB→10, 8 GB→8, CPU/<8 GB→6). Unknown hardware raises
        no cap (0); a window too small even for minimal observations still
        leaves 1 step so the user gets a bounded attempt.
        """
        from flask import current_app

        from app.model_config import get_model_config
        from app.utils import estimate_tokens

        max_steps = min(current_app.config.get("RLM_MAX_STEPS", 12), _STEP_LADDER_MAX)
        hw = _get_hardware()
        ladder = _resource_step_budget(hw)
        if ladder:
            max_steps = min(max_steps, ladder)

        cfg: dict[str, Any] = get_model_config("reasoning") or {}
        context = int(cfg.get("context_length") or 0)
        if not context:
            return int(max_steps)
        # A window that cannot hold the boot prompt plus one minimal
        # observation would die with «Request too long» — bound to 1 step.
        boot = sum(estimate_tokens(text, "reasoning", lang) for text in boot_texts if text)
        available = int(context * 0.95) - boot - 1000
        if available <= 0:
            return 1
        # Normal windows: steps are preserved and observations compress
        # (_obs_trunc_for_context). But the window must at least hold the
        # MINIMAL trajectory (one _MIN_OBS_CHARS observation per step):
        # shrink steps to what fits instead of failing mid-run.
        min_obs_tokens = estimate_tokens("а" * _MIN_OBS_CHARS, "reasoning", lang)
        min_per_step = min_obs_tokens + 300
        return int(max(1, min(max_steps, available // min_per_step)))

    def _auto_task_timeout(self, steps: int) -> int:
        """Derive the wall-clock deadline from the step budget and platform.

        RLM_TASK_TIMEOUT=0 selects this path: base overhead (sandbox start,
        JIT model load, corpus handout) plus per-step cost. CPU hosts get a
        ~3x per-step multiplier so slow generation is never cut short.
        """
        hw = _get_hardware()
        is_cpu = hw is None or getattr(hw, "platform", "cpu") == "cpu"
        base, per_step = _STEP_TIMEOUT_MODEL["cpu" if is_cpu else "gpu"]
        return base + max(1, steps) * per_step

    def build_user_prompt(self, question: str, corpus: dict[str, str], lang: str = "ru") -> str:
        lines = [f"{name} ({len(text)} chars)" for name, text in corpus.items()]
        listing = "\n".join(lines)
        if lang == "ru":
            return (
                f"Файлы корпуса:\n{listing}\n\nВопрос: {question}\n\n"
                "Изучи каждый файл корпуса, прежде чем отвечать: релевантная информация может быть в любом из них."
            )
        return f"Corpus files:\n{listing}\n\nQuestion: {question}"

    def broker_llm(self, prompt: str, text: str = "") -> str:
        from flask import current_app

        if self.lang == "ru":
            system_prompt = "Отвечай на русском языке, используя только предоставленный текст. Будь краток."
        else:
            system_prompt = "Answer the request using only the supplied text. Be concise."
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{prompt}\n\n---\n{text}"},
        ]
        max_tokens = current_app.config.get("RLM_SUB_MAX_TOKENS", 1024)
        result = self.app.modules["base"].llamacpp.chat(
            messages, model_type="reasoning", lang=self.lang, validate=False, temperature=0.2
        )
        if isinstance(result, str):
            return result
        content = result.get("content", "") or ""
        return content[: max_tokens * 4]

    def broker_web_fetch(self, query: str, broker: _RlmBroker) -> str:
        if broker.web_fetches >= broker.web_max_fetches:
            return "Web fetch limit reached for this analysis."
        if query in broker.seen_queries:
            return "That query was already searched earlier. Do not repeat lookups — use the gathered information and call final(answer)."
        broker.seen_queries.add(query)
        broker.web_fetches += 1
        search = self.app.modules.get("search")
        if not search or not getattr(search, "available", False):
            return "Web search is unavailable."
        results = search.search(query, lang=broker.lang, max_results=3) or []
        if not results:
            return "No web results found."
        parts = []
        for item in results[:3]:
            title = item.get("title", "")
            url = item.get("url", "")
            content = item.get("content", "") or ""
            parts.append(f"[{title}]({url})\n{content[:2000]}")
        return "\n\n".join(parts)

    def _execute_tool(
        self, name: str, args: dict[str, Any], sandbox: Any, broker: _RlmBroker
    ) -> tuple[str, str | None]:
        if name == "python":
            res = sandbox.exec(str(args.get("code", "")))
            if res.final is not None:
                return "", res.final
            return (res.output if res.ok else f"Error: {res.error}"), None
        if name == "llm":
            return broker.llm(str(args.get("prompt", "")), str(args.get("text", ""))), None
        if name == "web_fetch":
            return broker.web_fetch(str(args.get("query", ""))), None
        return f"Unknown tool: {name}", None

    def run(
        self,
        *,
        task: dict[str, Any],
        question: str,
        corpus: dict[str, str],
        user_id: str,
        session_id: str,
        lang: str,
        on_stage: Callable[[str, dict | None], None],
        is_cancelled: Callable[[], bool],
    ) -> RlmResult:
        from flask import current_app

        from app.queue import RedisRequestQueue
        from app.rlm_sandbox import RlmSandbox

        max_steps = current_app.config.get("RLM_MAX_STEPS", 12)
        code_timeout = current_app.config.get("RLM_CODE_TIMEOUT", 15)
        obs_trunc = current_app.config.get("RLM_OBS_TRUNC", 4000)
        sub_max_tokens = current_app.config.get("RLM_SUB_MAX_TOKENS", 1024)
        web_max_fetches = current_app.config.get("RLM_WEB_MAX_FETCHES", 5)
        task_timeout_cfg = current_app.config.get("RLM_TASK_TIMEOUT", 900)

        llamacpp = self.app.modules["base"].llamacpp
        tools = self.tool_definitions(lang)
        system = self.build_system_prompt(lang)
        user_prompt = self.build_user_prompt(question, corpus, lang=lang)
        max_steps = self._effective_max_steps([system, user_prompt], lang)
        from app.utils import estimate_tokens

        boot_tokens = sum(estimate_tokens(t, "reasoning", lang) for t in (system, user_prompt) if t)
        obs_trunc = _obs_trunc_for_context(max_steps, obs_trunc, boot_tokens)
        # Wall-clock deadline: -1 disables it entirely; 0 derives it from the
        # step budget and platform (CPU hosts get a proportionally larger
        # budget); any positive value is used as-is.
        if task_timeout_cfg == -1:
            deadline: float | None = None
        else:
            task_timeout = task_timeout_cfg if task_timeout_cfg > 0 else self._auto_task_timeout(max_steps)
            deadline = time.monotonic() + task_timeout
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.build_system_prompt(lang, max_steps=max_steps)},
            {"role": "user", "content": user_prompt},
        ]
        broker = _RlmBroker(self, lang, sub_max_tokens, web_max_fetches)
        sandbox = RlmSandbox(corpus, broker, code_timeout=code_timeout)
        self.lang = lang
        sandbox.start()
        trace: list[RlmTraceStep] = []
        try:
            for step in range(1, max_steps + 1):
                if is_cancelled():
                    return RlmResult("", trace, step, error="cancelled")
                if deadline is not None and time.monotonic() >= deadline:
                    return RlmResult("", trace, step, error="task timeout")
                # The nudge alone is ignorable — a model may spend its last
                # step on yet another tool call and the run dies at the limit.
                # On the final step tools are withheld entirely: the model
                # physically cannot call python/llm/web_fetch and must produce
                # a text answer, which is returned as the result.
                step_tools: list[dict[str, Any]] | None = tools
                if step >= max_steps:
                    step_tools = None
                elif step >= max_steps - 1:
                    messages.append(
                        {
                            "role": "user",
                            "content": f"Only {max_steps - step + 1} step(s) remain. Do NOT call any more tools. "
                            "Compose your final answer from the gathered information and call final(answer).",
                        }
                    )
                on_stage("rlm_step", {"step": step})
                response = llamacpp.chat(messages, model_type="reasoning", lang=lang, tools=step_tools, temperature=0.2)
                if isinstance(response, str):
                    # chat() returns a plain string both for a normal answer
                    # without tool calls and for backend errors. Only the
                    # latter must fail the run — otherwise the model's final
                    # answer is shown to the user as an error.
                    if RedisRequestQueue._is_llm_error_string(response):
                        return RlmResult("", trace, step, error=response)
                    response = {"content": response, "tool_calls": []}
                content = response.get("content", "") or ""
                tool_calls = response.get("tool_calls") or []
                if not tool_calls:
                    if content.strip():
                        return RlmResult(content.strip(), trace, step)
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": "Call final(answer) with your answer."})
                    continue
                messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
                for tc in tool_calls:
                    if is_cancelled():
                        return RlmResult("", trace, step, error="cancelled")
                    func = tc.get("function", {})
                    name = func.get("name", "")
                    try:
                        args = json.loads(func.get("arguments", "{}"))
                    except json.JSONDecodeError:
                        args = {}
                    if not isinstance(args, dict):
                        args = {}
                    if name == "final":
                        # Anti-premature-final guard: with a multi-file corpus the
                        # actor must have inspected the corpus at least once before
                        # answering, otherwise the answer may silently miss files
                        # that scored out of the semantic recall.
                        if len(corpus) > 1 and sandbox.python_exec_count == 0:
                            if lang == "ru":
                                guard_msg = (
                                    f"Финальный ответ отклонён: в корпусе {len(corpus)} файлов, но ни один не изучен. "
                                    "Прочитай каждый файл через python(context[имя_файла]) перед вызовом final(answer)."
                                )
                            else:
                                guard_msg = (
                                    f"Final answer rejected: the corpus has {len(corpus)} files but none were "
                                    "inspected. Read each corpus file with python(context[filename]) before "
                                    "calling final(answer)."
                                )
                            trace.append(RlmTraceStep(step, name, func.get("arguments", "")[:500], guard_msg))
                            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": guard_msg})
                            continue
                        answer = str(args.get("answer", "")).strip()
                        if not answer:
                            return RlmResult("", trace, step, error="empty final answer")
                        return RlmResult(answer, trace, step)
                    if name == "web_fetch":
                        on_stage("rlm_searching_web", None)
                    elif name == "llm":
                        on_stage("rlm_submodel", None)
                    observation, final_answer = self._execute_tool(name, args, sandbox, broker)
                    if final_answer is not None:
                        if not final_answer.strip():
                            return RlmResult("", trace, step, error="empty final answer")
                        return RlmResult(final_answer, trace, step)
                    observation = observation[:obs_trunc]
                    trace.append(RlmTraceStep(step, name, json.dumps(args)[:500], observation))
                    messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": observation})
        finally:
            sandbox.close()
        return RlmResult("", trace, max_steps, error="step limit reached")
