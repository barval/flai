"""RLM (Recursive Language Models) deep-analysis module."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.utils import format_prompt

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
        return format_prompt("rlm.template", {"max_steps": max_steps}, lang=lang) or ""

    def _effective_max_steps(self, boot_texts: list[str], lang: str) -> int:
        """Cap the actor loop so its worst-case trajectory fits the reasoning window.

        One step costs one assistant tool-call turn plus one observation of up
        to RLM_OBS_TRUNC chars. The 0.95 factor mirrors _validate_prompt's hard
        limit; the 1000-token reserve keeps the run inside the window instead of
        dying with «Request too long». Unknown/missing window -> configured cap.
        """
        from flask import current_app

        from app.model_config import get_model_config
        from app.utils import estimate_tokens

        max_steps = current_app.config.get("RLM_MAX_STEPS", 12)
        cfg = get_model_config("reasoning") or {}
        context = cfg.get("context_length") or 0
        if not context:
            return max_steps
        obs_chars = current_app.config.get("RLM_OBS_TRUNC", 4000)
        boot = sum(estimate_tokens(text, "reasoning", lang) for text in boot_texts if text)
        step_budget = 300 + estimate_tokens("а" * obs_chars, "reasoning", lang)
        available = int(context * 0.95) - boot - 1000
        if available <= 0:
            return 1
        return max(1, min(max_steps, available // max(1, step_budget)))

    def build_user_prompt(self, question: str, corpus: dict[str, str]) -> str:
        lines = [f"{name} ({len(text)} chars)" for name, text in corpus.items()]
        listing = "\n".join(lines)
        return f"Corpus files:\n{listing}\n\nQuestion: {question}"

    def broker_llm(self, prompt: str, text: str = "") -> str:
        from flask import current_app

        messages = [
            {"role": "system", "content": "Answer the request using only the supplied text. Be concise."},
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

        llamacpp = self.app.modules["base"].llamacpp
        tools = self.tool_definitions(lang)
        system = self.build_system_prompt(lang)
        max_steps = self._effective_max_steps([system, self.build_user_prompt(question, corpus)], lang)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.build_system_prompt(lang, max_steps=max_steps)},
            {"role": "user", "content": self.build_user_prompt(question, corpus)},
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
                if step >= max_steps - 1:
                    messages.append(
                        {
                            "role": "user",
                            "content": f"Only {max_steps - step + 1} step(s) remain. Do NOT call any more tools. "
                            "Compose your final answer from the gathered information and call final(answer).",
                        }
                    )
                on_stage("rlm_step", {"step": step})
                response = llamacpp.chat(messages, model_type="reasoning", lang=lang, tools=tools, temperature=0.2)
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
