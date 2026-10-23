# app/tasks/runner.py
"""Executes a single direct task step and its checkpoint verifier (Tasks subsystem).

The TaskExecutor (app/tasks/executor.py, Task 7) calls ``run_direct_step`` for
each direct step (tool: llm | search | rag | calculate) and ``run_checkpoint`` to
judge the step result against its readiness criterion.

Runner contract (Task 4 review ruling): the registry/tool runners return raw
values; ``run_direct_step`` normalizes to ``str`` at ONE boundary after tool
execution.

LLM steps stream through the same client the workers use (the reasoning model
is JIT-loaded by llama-swap) and poll ``cancel_check`` between chunks; a cancel
raises the module-level ``StepAborted`` — no partial text is ever returned.
Search/calculate are fast (<30 s): cancel is checked before and after the call
only; a race-cancel returns the completed result with status ``aborted``.

Both functions self-time with ``time.monotonic()``: ``exec_seconds`` of
``run_direct_step`` covers the tool execution only — ``run_checkpoint`` returns
its own ``exec_seconds`` and the executor adds it to the task budget itself.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Generator
from typing import Any

from flask import Flask

from app.model_config import get_model_config
from app.utils import begin_usage_account, finish_usage_account

logger = logging.getLogger(__name__)

# The verifier prompt keeps the step result bounded — a huge intermediate
# result would waste the reasoning window on repeated context.
CHECKPOINT_RESULT_MAX_CHARS = 8000
# A media reference may be a whole multi-part plan; the step's own goal and
# criterion are rendered as separate fields and never truncated away.
MEDIA_REFERENCE_MAX_CHARS = 16000

# Real token usage of the LAST _call_reasoning on this thread (executor
# threads run one step at a time): _run_llm_step stashes it, run_direct_step
# reads it into the returned payload (→ the step row's extra → the UI header).
_last_call_usage = threading.local()


class StepAborted(Exception):  # noqa: N818 - name fixed by the Task 6 implementer brief
    """A task step was cancelled mid-execution (a new chat request preempted it)."""


def _result_to_str(raw: Any) -> str:
    """Normalize a tool's raw value to str at a single boundary (Task 4 ruling)."""
    return raw if isinstance(raw, str) else str(raw)


def recall_slm_facts(app: Flask, query: str, user_id: str, lang: str, limit: int | None = None) -> str:
    """Recall the user's long-term memory facts for ``query`` (Tasks ↔ SLM, v12.6).

    Mirrors the chat's ``BaseModule._get_context_for_model`` recall exactly: the
    SLM module is optional (absent → empty section), one ``recall`` call with a
    doubled limit is filtered locally by fact type (session-specific first), and
    the section is capped at ``limit`` facts (default ``SLM_RECALL_LIMIT``, like
    chat). The returned text is an inline prompt section — SLM is a context
    service the server resolves, never a tool the model calls. Errors degrade to
    an empty section, never a task failure.
    """
    if not query or not user_id:
        return ""
    slm = app.modules.get("slm")
    if slm is None:
        return ""
    if limit is None:
        limit = int(app.config.get("SLM_RECALL_LIMIT", 7))
    try:
        raw = slm.recall(query, limit=limit * 2, profile=user_id)
    except Exception as e:
        logger.warning(f"task SLM recall failed for profile {user_id}: {e}")
        return ""
    if not raw:
        return ""
    session_facts = [f for f in raw if f.get("metadata", {}).get("fact_type") == "session_specific"]
    general_facts = [f for f in raw if f.get("metadata", {}).get("fact_type") != "session_specific"]
    all_facts = (session_facts + general_facts)[:limit]
    if not all_facts:
        return ""
    header = (
        "Дополнительная информация из долговременной памяти:"
        if lang == "ru"
        else "Additional context from long-term memory:"
    )
    lines = [header] + [f"- {f.get('content', f.get('text', ''))}" for f in all_facts]
    return "\n" + "\n".join(lines)


def _reasoning_chat(
    app: Flask,
    messages: list[dict],
    lang: str = "ru",
    temperature: float | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> Generator[str | dict[str, Any], None, None]:
    """Yield reasoning-model chunks via the same client the workers use.

    The client resolves the reasoning model config itself (JIT load through
    llama-swap) exactly as ``RedisRequestQueue`` does via ``base.llamacpp``.
    Runner-facing seam: tests patch this helper to isolate the LLM client.
    ``status_callback`` (optional) is forwarded to the client and fires with
    ``"generating"`` once the model is loaded — the plan phase turns it into
    the live stage event.
    """
    base = app.modules.get("base")
    if base is None or not hasattr(base, "llamacpp"):
        raise RuntimeError("base module (llamacpp client) is unavailable")
    yield from base.llamacpp.chat_stream(
        messages, model_type="reasoning", lang=lang, temperature=temperature, status_callback=status_callback
    )


def _call_reasoning(
    app: Flask,
    prompt: str,
    cancel_check: Callable[[], bool],
    lang: str = "ru",
    temperature: float | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> tuple[str, dict[str, int]]:
    """Stream one reasoning-model call for a step; abort mid-stream on cancel.

    Retries ONCE on transient llama-swap errors (model just got unloaded,
    config reload in progress) — the model reload after the first attempt is
    fast because JIT load just happened.

    Opens a per-call usage account (keyed by the prompt hash) so the real
    stream-usage totals (stream_options.include_usage) are attributable to
    THIS call; returns ``(text, usage)``. The executor stores usage into the
    step's extra for the chat-style header (🔢 tokens).
    """
    messages = [{"role": "user", "content": prompt}]
    transient_attempt = 0
    busy_attempt = 0
    busy_deadline = time.monotonic() + _gpu_wait_budget(app)
    while True:
        account_id = f"task-call:{uuid.uuid4()}"
        begin_usage_account(account_id)
        text = ""
        for chunk in _reasoning_chat(app, messages, lang, temperature=temperature, status_callback=status_callback):
            if cancel_check():
                finish_usage_account(account_id)
                raise StepAborted("step cancelled during reasoning generation")
            if isinstance(chunk, dict):
                continue
            text += chunk
        if cancel_check():
            finish_usage_account(account_id)
            raise StepAborted("step cancelled at end of reasoning generation")
        usage = finish_usage_account(account_id) or {}
        totals = {k: int(v) for k, v in usage.items() if isinstance(v, (int, float)) and v > 0}
        # A busy GPU is a state to wait out, never a step result: when the
        # client's ensure_vram_for deadline expires it yields the localized
        # «GPU memory unavailable» string, and that text would otherwise ride
        # into the step (or the plan review) as if the model had answered.
        if _is_gpu_busy_text(text):
            if time.monotonic() >= busy_deadline:
                app.logger.warning(f"call_reasoning: GPU still busy after the wait budget — giving up: {text[:120]}")
                return text, totals
            delay = min(_GPU_RETRY_MAX_S, _GPU_RETRY_STEP_S * (2 ** min(busy_attempt, 4)))
            busy_attempt += 1
            app.logger.info(f"call_reasoning: GPU busy — waiting {delay}s before retry ({text[:80]!r})")
            _wait_cancellable(delay, cancel_check)
            continue
        # Retry once on transient llama-swap errors (model unloaded / config reload).
        if (
            transient_attempt == 0
            and _is_error_text(text)
            and (
                "model unloaded" in text
                or "была выгружен" in text
                or "shutting down" in text
                or "перезагружает" in text
            )
        ):
            transient_attempt += 1
            app.logger.warning(f"call_reasoning transient error on attempt 1, retrying: {text[:200]}")
            continue
        return text, totals


_GPU_BUSY_PREFIXES = ("GPU memory ", "Память GPU ", "Проверка памяти GPU")
_GPU_RETRY_STEP_S = 5.0
_GPU_RETRY_MAX_S = 30.0


def _is_gpu_busy_text(text: str) -> bool:
    """Is this the client's «VRAM is not free yet» string (never an LLM answer)?

    Prefix match, mirroring ``_is_llm_error_string``: the client emits these
    strings only from its own error paths.
    """
    return isinstance(text, str) and text.startswith(_GPU_BUSY_PREFIXES)


def _gpu_wait_budget(app: Flask) -> float:
    """How long ONE reasoning call may wait for the GPU (``TASK_VRAM_WAIT_S``)."""
    try:
        return float(app.config.get("TASK_VRAM_WAIT_S", 600))
    except Exception:
        return 600.0


def _wait_cancellable(seconds: float, cancel_check: Callable[[], bool]) -> None:
    """Sleep in slices so a chat preemption aborts the GPU wait immediately."""
    deadline = time.monotonic() + seconds
    while True:
        if cancel_check():
            raise StepAborted("step cancelled while waiting for the GPU")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(1.0, remaining))


def _is_error_text(text: str) -> bool:
    """Route through the queue's LLM-error detector (⚠️ prefix / known patterns)."""
    from app.queue import RedisRequestQueue

    return RedisRequestQueue._is_llm_error_string(text)


def _run_llm_step(
    app: Flask,
    step: dict[str, Any],
    context_digest: str,
    cancel_check: Callable[[], bool],
    lang: str,
    response_style: str,
    task_request: str,
    plan_text: str,
    user_answers: str,
    user_id: str = "",
) -> str:
    """Build the step prompt from the task_step template and stream the model."""
    from app.utils import format_prompt, get_current_time_in_timezone
    from modules.base import get_style_instruction, response_language_name

    params = {
        "task_request": task_request,
        "plan": plan_text,
        "step_goal": step.get("goal", ""),
        "step_tool": step.get("tool", "llm"),
        "step_checkpoint": step.get("checkpoint_criteria", ""),
        "previous_results": context_digest,
        "user_answers": user_answers,
        "slm_facts": recall_slm_facts(app, task_request or step.get("goal", ""), user_id, lang),
        "response_language": response_language_name(lang),
        "response_style": get_style_instruction(lang, response_style),
        "current_time": get_current_time_in_timezone(app) or "",
    }
    prompt = format_prompt("task_step.template", params, lang=lang)
    if not prompt:
        raise RuntimeError("task_step prompt template unavailable")
    call_out = _call_reasoning(app, prompt, cancel_check, lang=lang)
    text, usage = call_out if isinstance(call_out, tuple) else (call_out, {})
    _last_call_usage.value = usage  # thread-local stash, read by run_direct_step
    return text


def _run_continuity_step(
    app: Flask,
    step: dict[str, Any],
    context_digest: str,
    cancel_check: Callable[[], bool],
    lang: str,
    task_request: str,
    plan_text: str,
) -> str:
    """Extract the visual continuity sheet (tool ``continuity``) via the reasoning model.

    Mirrors ``_run_llm_step``'s LLM plumbing (same reasoning-model call, no
    GPU lock, cancel polled between chunks) with the dedicated
    ``task_continuity`` template. The corpus is the task request plus the plan
    goals and the previous step results, the latter two stripped of
    source-reference markers (``sanitize_reference_markers``) so document
    line-range artifacts never ride into the sheet. A merge extraction puts
    the current sheet into ``step["extra"]["existing_sheet"]`` (the extraction
    core's seam): the prompt then demands existing ids/looks kept verbatim,
    new entities only, style unchanged.

    The raw model output is returned UNTRUNCATED — the over-budget policy
    (one corrective re-extraction, then accept whole) belongs to the
    extraction core, never this level. Unparseable or invalid JSON raises
    ``RuntimeError`` (→ ``status="error"``, ``checkpoint=None``).
    """
    from app.tasks.continuity import parse_bible, sanitize_reference_markers
    from app.utils import format_prompt
    from modules.base import response_language_name

    step_extra = step.get("extra") or {}
    existing = step_extra.get("existing_sheet")
    existing_section = ""
    if isinstance(existing, dict) and any(
        existing.get(key) for key in ("style", "characters", "places", "props")
    ):
        existing_section = json.dumps(existing, ensure_ascii=False, indent=2)
    params = {
        "task_request": task_request,
        "plan": sanitize_reference_markers(plan_text or ""),
        "step_goal": step.get("goal", ""),
        "step_checkpoint": step.get("checkpoint_criteria", ""),
        "previous_results": sanitize_reference_markers(context_digest or ""),
        "response_language": response_language_name(lang),
        "max_chars": int(app.config.get("TASK_CONTINUITY_MAX_CHARS", 2000)),
        "existing_sheet": existing_section,
    }
    prompt = format_prompt("task_continuity.template", params, lang=lang)
    if not prompt:
        raise RuntimeError("task_continuity prompt template unavailable")
    call_out = _call_reasoning(app, prompt, cancel_check, lang=lang)
    text, usage = call_out if isinstance(call_out, tuple) else (call_out, {})
    _last_call_usage.value = usage  # thread-local stash, read by run_direct_step
    if _is_error_text(text):
        # A persistent LLM error surfaces as itself, not as a parse failure.
        raise RuntimeError(text)
    if parse_bible(text) is None:
        raise RuntimeError("continuity sheet is not a valid JSON object")
    return text


# A task step that searches documents waits for the task's own attachments to
# leave the indexing states. Chat parity: a chat answer over an attached
# document is produced only after the document finished indexing
# (``_process_doc_chat_task`` indexes it before re-queueing the question), so a
# task step must not search a half-indexed document and report «no relevant
# documents». The bound is the indexing pipeline's own worst case — the OCR
# budget, because a scanned PDF is recognised page by page.
_DOC_INDEX_POLL_S = 2


def _doc_needs_repair(doc: dict, app=None) -> bool:
    import os as _os
    import time as _time

    if not doc:
        return False
    idx = doc.get("index_status")
    if idx == "failed":
        return True
    if idx == "indexing":
        started = doc.get("indexing_started_at")
        if started:
            try:
                # started_at is datetime/timestamp-like? handle naive
                # store gives datetime objects in some cases? get_user_documents returns dicts; check
                ts = started.timestamp() if hasattr(started, "timestamp") else float(started) if started else 0
            except Exception:
                ts = 0
            if ts and (_time.time() - ts) > 1800:  # 30 min
                return True
        else:
            # no start time, treat as potentially stuck
            pass
    file_path = doc.get("file_path")
    if file_path and app is not None:
        try:
            docs_folder = app.config.get("DOCUMENTS_FOLDER", "data/documents")
            full_path = _os.path.join(docs_folder, file_path)
            name, ext = _os.path.splitext(full_path)
            recognized = name + ".recognized_text"
            if not _os.path.exists(recognized):
                # likely image/PDF without sidecar; needs repair
                low = (ext or "").lower()
                if low in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".pdf"):
                    return True
        except Exception:
            pass
    return False


def wait_for_documents_indexed(app: Flask, user_id: str, doc_ids: list[str], cancel_check=None) -> None:
    """Block until every attached document is indexed (or fails after a single repair)."""
    import time as _time

    from app.db import INDEX_STATUS_FAILED, INDEX_STATUS_INDEXED, get_user_documents
    from app.queue import RedisRequestQueue

    pending = {str(d) for d in (doc_ids or []) if d}
    if not pending:
        return
    bound = app.config.get("OCR_TIME_BUDGET_S", 1800) or 1800
    deadline = _time.monotonic() + bound
    repaired = set()
    queue = RedisRequestQueue(app, start_workers=False) if hasattr(RedisRequestQueue, "__init__") else None
    while pending and _time.monotonic() < deadline:
        if cancel_check is not None and cancel_check():
            raise StepAborted("step cancelled while waiting for document indexing")
        try:
            docs = get_user_documents(user_id) or []
        except Exception as e:
            logger.warning(f"document index status check failed: {e}")
            return
        doc_map = {str(d.get("id")): d for d in docs}
        # detect repairable in pending
        to_repair = []
        for d in list(pending):
            dd = doc_map.get(d)
            if dd and dd.get("index_status") == INDEX_STATUS_INDEXED:
                pending.discard(d)
                continue
            if dd and dd.get("index_status") == INDEX_STATUS_FAILED and d not in repaired:
                to_repair.append(d)
                repaired.add(d)
                continue
            if _doc_needs_repair(dd, app) and d not in repaired:
                to_repair.append(d)
                repaired.add(d)
                continue
            # stuck indexing? check age
            if dd and dd.get("index_status") == "indexing":
                started = dd.get("indexing_started_at")
                if started:
                    try:
                        ts = started.timestamp() if hasattr(started, "timestamp") else float(started)
                    except Exception:
                        ts = 0
                    if ts and (_time.time() - ts) > 1800 and d not in repaired:
                        to_repair.append(d)
                        repaired.add(d)
                # else leave pending
        # enqueue repairs
        if to_repair:
            try:
                docs_folder = app.config.get("DOCUMENTS_FOLDER", "data/documents")
                for rid in to_repair:
                    dd = doc_map.get(rid) or {}
                    file_path = dd.get("file_path")
                    if not file_path:
                        continue
                    import os as _os

                    full_path = _os.path.join(docs_folder, file_path)
                    ext = _os.path.splitext(full_path)[1].lower()
                    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".pdf"):
                        # treat image/pdf as needing description/indexing
                        rq = {
                            "id": str(__import__("uuid").uuid4()),
                            "session_id": str(__import__("uuid").uuid4()),
                            "user_id": user_id,
                            "lang": "ru",
                            "data": {"type": "describe_document_image", "doc_id": rid, "file_path": file_path},
                            "user_class": 5,
                        }
                    else:
                        rq = {
                            "id": str(__import__("uuid").uuid4()),
                            "session_id": str(__import__("uuid").uuid4()),
                            "user_id": user_id,
                            "lang": "ru",
                            "data": {"type": "index_document", "doc_id": rid, "file_path": file_path},
                            "user_class": 5,
                        }
                    queue.add_request(
                        user_id=user_id, session_id=rq["session_id"], request_data=rq["data"], user_class=5, lang="ru"
                    )
                _time.sleep(2)
                continue
            except Exception as e:
                logger.warning(f"document repair enqueue failed: {e}")
        # update pending
        settled = {str(d.get("id")): d.get("index_status") for d in doc_map.values() if str(d.get("id")) in pending}
        pending = {d for d in pending if settled.get(d) not in (INDEX_STATUS_INDEXED, INDEX_STATUS_FAILED)}
        if pending:
            _time.sleep(_DOC_INDEX_POLL_S)
    if pending:
        logger.warning(f"documents still indexing after {bound}s: {sorted(pending)}")


def _run_rag_step(
    app: Flask,
    step: dict[str, Any],
    user_id: str,
    lang: str,
    doc_ids: list[str] | None = None,
    cancel_check=None,
) -> str:
    """Document-search runner: query from step extra (fallback: goal), fragments out.

    Mirrors ``RedisRequestQueue._process_rag_task`` retrieval: the task's
    attached documents scope the search (``doc_ids``), otherwise every indexed
    document of the user is searched with file coverage; fragments below the
    relevance threshold are dropped except coverage chunks. The following llm
    step composes the answer from the returned fragments — this step never
    generates text itself (capability parity with the chat RAG path).

    Attached documents are waited for first (they were uploaded with the task
    moments ago) — the search must never run against a half-indexed document.
    """
    from flask_babel import force_locale
    from flask_babel import gettext as _

    rag = app.modules.get("rag")
    if rag is None or not getattr(rag, "available", False):
        raise RuntimeError("document search module is not enabled")
    wait_for_documents_indexed(app, user_id, doc_ids or [], cancel_check)
    step_extra = step.get("extra") or {}
    query = str(step_extra.get("query") or step.get("goal") or "")
    threshold = app.config.get("RAG_RELEVANCE_THRESHOLD_DEFAULT", 0.3)
    chunks, scores = rag.search(user_id, query, top_k=20, file_coverage=True, doc_ids=doc_ids)
    filtered = [(c, s) for c, s in zip(chunks, scores, strict=False) if s >= threshold or c.get("coverage")]
    if not filtered:
        with force_locale(lang):
            return _("No relevant documents found for query: {query}").format(query=query)
    with force_locale(lang):
        source_label = _("Source")
    parts = []
    semantic_seen = 0
    for chunk, score in filtered:
        if not chunk.get("coverage"):
            if semantic_seen >= 15:
                continue
            semantic_seen += 1
        filename = chunk.get("filename", "?")
        text = chunk.get("text", str(chunk))
        parts.append(f"[{source_label}: {filename} (score: {score:.2f})]\n{text}")
    return "\n\n".join(parts)


def _run_search_step(app: Flask, step: dict[str, Any], user_id: str, lang: str) -> str:
    """Web-search runner: query from step extra (fallback: goal), formatted context out.

    Mirrors ``RedisRequestQueue._process_search_task``: the per-user Tavily key
    is fetched by the caller (search_with_fallback degrades to SearXNG without
    one) and results go through ``format_results_context`` with the same
    context budget the search pipeline uses.
    """
    from app.tavily_keys import get_tavily_key

    search = app.modules.get("search")
    if search is None:
        raise RuntimeError("web search module is not enabled")
    step_extra = step.get("extra") or {}
    query = str(step_extra.get("query") or step.get("goal") or "")
    api_key = get_tavily_key(user_id) if user_id else None
    results, _provider = search.search_with_fallback(query, lang=lang, api_key=api_key)
    base = app.modules.get("base")
    max_chars = base.get_search_context_limit() if base and hasattr(base, "get_search_context_limit") else 10000
    return search.format_results_context(results or [], lang=lang, max_chars=max_chars)


def _run_history_step(app: Flask, step: dict[str, Any], user_id: str, lang: str) -> str:
    """Conversation-history runner: past-dialogue fragments matching the query.

    Mirrors ``RedisRequestQueue._process_history_task`` retrieval (CPU-only
    PostgreSQL full-text search, no embeddings, no GPU): matching fragments
    from the user's previous sessions are formatted with the same caps. A
    wildcard goal or no direct matches degrades to a compact overview drawn
    from representative user messages. The following llm step composes the
    answer from the fragments — this step never generates text itself.
    """
    from flask_babel import force_locale
    from flask_babel import gettext as _

    from modules.history import format_history_context, get_history_overview, search_history

    step_extra = step.get("extra") or {}
    query = str(step_extra.get("query") or step.get("goal") or "")
    max_chars = int(app.config.get("HISTORY_MAX_RESULTS_CHARS", 5000))
    history_context = ""
    if query.strip() != "*":
        fragments = search_history(
            user_id,
            query,
            limit=int(app.config.get("HISTORY_SEARCH_LIMIT", 5)),
            max_message_chars=int(app.config.get("HISTORY_MAX_MESSAGE_CHARS", 20000)),
        )
        history_context = format_history_context(fragments, max_chars=max_chars)
    if not history_context:
        overview = get_history_overview(user_id, max_chars=max_chars)
        history_context = format_history_context(overview, max_chars=max_chars)
    if not history_context:
        with force_locale(lang):
            return _("No previous conversations found on topic: {topic}").format(topic=query or "-")
    return history_context


def _run_calculate_step(step: dict[str, Any]) -> Any:
    """Calculator runner via the capabilities registry (raw value out).

    The planner should put the bare expression in the goal; when prose slips
    in ("calculate the expression 2 + 2 * 2") the expression is extracted
    from the text before safe_eval — a prose goal is a SyntaxError otherwise.
    A variable name the calculator does not know (planner-drift like
    ``(rate_eur + rate_usd) / 2``) raises a readable error instead of the
    bare "tool error" the user saw: the inability is a prompt/planner bug,
    not a runtime crash.
    """
    from app.tasks.tools import TOOL_RUNNERS

    runner = TOOL_RUNNERS.get("calculate")
    if runner is None:
        raise RuntimeError("calculate runner is not registered")
    step_extra = step.get("extra") or {}
    expression = str(step_extra.get("expression") or step.get("goal") or "")
    try:
        return runner(expression)
    except SyntaxError:
        extracted = _extract_math_expression(expression)
        if not extracted or extracted == expression:
            raise
        return runner(extracted)
    except (ValueError, TypeError) as e:
        # safe_eval rejects variable names ("Unknown variable: rate_eur") and
        # unknown functions. Surface the reason in the step's error text so
        # the user (and the verifier) see what actually failed.
        raise ValueError(f"calculator: {e}") from None


_MATH_TOKEN_RE = re.compile(r"[-+*/().,0-9\s^%√πe]+", re.UNICODE)


def _extract_math_expression(text: str) -> str:
    """Longest contiguous math-ish chunk containing at least one digit+operator.

    Used only as a fallback when safe_eval rejects the raw goal as prose.
    """
    candidates = []
    buf = ""
    for ch in text:
        if _MATH_TOKEN_RE.match(ch):
            buf += ch
        else:
            if buf.strip():
                candidates.append(buf.strip())
            buf = ""
    if buf.strip():
        candidates.append(buf.strip())
    candidates = [c for c in candidates if any(d.isdigit() for d in c)]
    if not candidates:
        return ""
    # A prose sentence leaves its trailing period inside the chunk («2 + 2 * 2.»)
    # — safe_eval reads it as a float literal (6.0). Strip trailing dots so the
    # result keeps its natural int shape (6).
    best = max(candidates, key=len)
    while best.endswith("."):
        best = best[:-1].rstrip()
    return best.replace(",", ".").strip()


def run_direct_step(
    app: Flask,
    step: dict[str, Any],
    context_digest: str,
    cancel_check: Callable[[], bool],
    user_id: str = "",
    lang: str = "ru",
    response_style: str = "neutral",
    task_request: str = "",
    plan_text: str = "",
    user_answers: str = "",
    doc_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Run ONE direct step (tool: llm | search | rag | history | calculate | continuity) and its checkpoint.

    Returns ``{"result": str, "exec_seconds": float,
    "status": "passed"|"failed"|"aborted"|"error",
    "checkpoint": {"verdict": bool, "explanation": str, "exec_seconds": float}|None}``.

    ``exec_seconds`` covers the tool execution only — the checkpoint's own
    duration is inside ``checkpoint["exec_seconds"]`` for the executor to add.

    Raises ``StepAborted`` when the step is cancelled (pre-call check or an
    LLM cancel mid-stream) — no partial result is ever returned.
    """
    with app.app_context():
        if cancel_check():
            raise StepAborted("step cancelled before execution")
        tool = str(step.get("tool", "llm"))
        start = time.monotonic()
        try:
            if tool == "llm":
                raw = _run_llm_step(
                    app,
                    step,
                    context_digest,
                    cancel_check,
                    lang,
                    response_style,
                    task_request,
                    plan_text,
                    user_answers,
                    user_id,
                )
            elif tool == "search":
                raw = _run_search_step(app, step, user_id, lang)
            elif tool == "rag":
                raw = _run_rag_step(app, step, user_id, lang, doc_ids, cancel_check)
            elif tool == "history":
                raw = _run_history_step(app, step, user_id, lang)
            elif tool == "calculate":
                raw = _run_calculate_step(step)
            elif tool == "continuity":
                raw = _run_continuity_step(app, step, context_digest, cancel_check, lang, task_request, plan_text)
            else:
                raise RuntimeError(f"unknown direct step tool: {tool}")
        except StepAborted:
            raise
        except Exception as e:
            err_text = str(e) or "tool error"
            # calculate/continuity surface the failure reason (an unknown
            # variable, an unparseable sheet) instead of a bare "tool error".
            message = err_text if tool in ("calculate", "continuity") else ""
            logger.error(f"direct step {step.get('id', '?')} tool={tool} failed: {e}")
            return {
                "result": message,
                "exec_seconds": round(time.monotonic() - start, 2),
                "status": "error",
                "checkpoint": None,
            }
        exec_seconds = round(time.monotonic() - start, 2)
        result = _result_to_str(raw)
        if tool == "llm" and _is_error_text(result):
            return {"result": result, "exec_seconds": exec_seconds, "status": "error", "checkpoint": None}
        if cancel_check():
            # Race-cancel on a fast tool: the work is done, the result is kept
            # but reported aborted so the executor does not advance the plan.
            return {"result": result, "exec_seconds": exec_seconds, "status": "aborted", "checkpoint": None}
        checkpoint = run_checkpoint(app, step, result, lang=lang)
        payload = {
            "result": result,
            "exec_seconds": exec_seconds,
            "status": "passed" if checkpoint.get("verdict") else "failed",
            "checkpoint": checkpoint,
        }
        if tool in ("llm", "continuity"):
            # Chat-style header data (model + 🔢 tokens) for the conversation
            # view: both tools stream the reasoning model themselves.
            config = get_model_config("reasoning") or {}
            payload["model_name"] = str(config.get("model_name") or "")
            payload["usage"] = dict(getattr(_last_call_usage, "value", {}) or {})
        else:
            # A tool produced the content: the checkpoint verifier is the only
            # model call made for this step, so the header reports IT.
            payload["model_name"] = str(checkpoint.get("model_name") or "")
            payload["usage"] = dict(checkpoint.get("usage") or {})
        return payload


def _parse_verdict(reply: str) -> dict[str, Any]:
    """Parse a verifier reply into ``{"verdict": bool, "explanation": str}``
    plus the optional "needs_user_input" verdict: ``{"question": str}``.

    Tolerates thinking-tag residue, markdown fences and any prose around the
    JSON object. Any parse failure is a failed verdict — never a crash.
    """
    from app.llamacpp_client import _strip_generic_reasoning, _strip_thinking_tags

    text = _strip_thinking_tags(_strip_generic_reasoning(reply or "")).strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {"verdict": False, "explanation": "unparseable checkpoint reply"}
    try:
        data = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return {"verdict": False, "explanation": "unparseable checkpoint reply"}
    if not isinstance(data, dict):
        return {"verdict": False, "explanation": "unparseable checkpoint reply"}
    # Strict verdict coercion: a truthy string ("false", free text) must never
    # pass as True. Only a real bool (or the exact words true/false) counts;
    # anything else is an unparseable reply.
    raw_verdict = data.get("verdict")
    if isinstance(raw_verdict, bool):
        verdict = raw_verdict
    elif isinstance(raw_verdict, str) and raw_verdict.strip().lower() in ("true", "false"):
        verdict = raw_verdict.strip().lower() == "true"
    else:
        return {"verdict": False, "explanation": "unparseable checkpoint reply"}
    parsed = {"verdict": verdict, "explanation": str(data.get("explanation", ""))}
    # Severity lets the caller accept an artifact on minor differences only
    # (style/lighting/small details) instead of regenerating it.
    severity = str(data.get("severity") or "").strip().lower()
    if severity in ("none", "minor", "critical"):
        parsed["severity"] = severity
    mismatches = data.get("mismatches")
    if isinstance(mismatches, list):
        parsed["mismatches"] = [m for m in mismatches if isinstance(m, dict)][:20]
    # Repair policy: a local defect may be fixed by editing this same picture;
    # score ranks the candidates when every attempt fails (best effort).
    parsed["repairable"] = data.get("repairable") is True
    raw_score = data.get("score")
    try:
        parsed["score"] = max(0, min(100, int(raw_score)))
    except (TypeError, ValueError):
        parsed["score"] = None
    # Third verdict: user data missing — the question goes to the user and the
    # step re-runs with the answer. Only honored on a failed verdict (the
    # templates emit it as verdict=false + needs_user_input=true).
    if not verdict and data.get("needs_user_input") is True:
        question = str(data.get("question") or "").strip()
        if question:
            parsed["needs_user_input"] = True
            parsed["question"] = question
    return parsed


_CHECKPOINT_SENTINELS = {
    "unparseable checkpoint reply": "the verifier returned an unreadable verdict",
    "checkpoint verification failed": "the verification could not be started",
    "verifier answered in another language": "the verifier answered in another language",
}


_FOREIGN_WORD_RES = {"ru": re.compile(r"[A-Za-z]{2,}"), "en": re.compile(r"[\u0400-\u04FF]{2,}")}
_TARGET_SCRIPT_RES = {"ru": re.compile(r"[\u0400-\u04FF]"), "en": re.compile(r"[A-Za-z]")}


def _needs_language_retry(text: str, lang: str) -> bool:
    """True when a model-written verdict reason is not in the profile language.

    The verifier gets ``response_language`` in its prompt, but a weak model
    sometimes answers in English anyway; the explanation is stored verbatim and
    printed in the task view, so a foreign reason is a visible defect.
    """
    text = (text or "").strip()
    if not text or lang not in _FOREIGN_WORD_RES:
        return False
    if _TARGET_SCRIPT_RES[lang].search(text):
        return False
    return len(_FOREIGN_WORD_RES[lang].findall(text)) >= 3


def _language_reminder(lang: str) -> str:
    """Corrective sentence appended for the single wording retry."""
    if lang == "ru":
        return "Повтори ответ и напиши поле explanation строго на русском языке."
    return "Repeat the answer and write the explanation strictly in English."


def _verify_with_language_guard(base: Any, prompt: str, lang: str) -> dict[str, Any]:
    """Ask the verifier once, retrying only when the reason came in another language.

    The verdict itself is never re-decided by the retry — only the wording is
    replaced. When the retry is foreign too, a localized sentinel is stored and
    the original text goes to the log for diagnostics.
    """
    reply = base.call_llamacpp(
        [{"role": "user", "content": prompt}],
        model_type="reasoning",
        lang=lang,
        temperature=0.2,
    )
    verdict = _parse_verdict(str(reply))
    if not _needs_language_retry(str(verdict.get("explanation") or ""), lang):
        return verdict

    retry = _parse_verdict(
        str(
            base.call_llamacpp(
                [
                    {"role": "user", "content": prompt},
                    {"role": "user", "content": _language_reminder(lang)},
                ],
                model_type="reasoning",
                lang=lang,
                temperature=0.2,
            )
        )
    )
    if not _needs_language_retry(str(retry.get("explanation") or ""), lang):
        verdict["explanation"] = retry.get("explanation") or verdict.get("explanation")
        return verdict

    logger.warning(f"verifier answered in another language (profile {lang!r}): {verdict.get('explanation')!r}")
    verdict["explanation"] = "verifier answered in another language"
    return verdict


def localize_checkpoint_explanation(explanation: str, lang: str = "ru") -> str:
    """Translate an internal sentinel; keep a model-written reason as it is.

    ``_parse_verdict`` reports failures in English so the executor can match
    them. Those markers are internal and must never reach a user: the executor
    stores the explanation in the step row and the task view prints it. Every
    other explanation is the verifier's own wording, written in the requested
    language already.
    """
    from app.llamacpp_client import _tr

    msgid = _CHECKPOINT_SENTINELS.get(str(explanation or "").strip())
    return _tr(msgid, lang=lang) if msgid else explanation


def run_checkpoint(app: Flask, step: dict[str, Any], result: str, lang: str = "ru") -> dict[str, Any]:
    """Judge a step result against its readiness criterion (short reasoning call).

    Returns ``{"verdict": bool, "explanation": str, "exec_seconds": float}``.
    A non-verdict system failure (template/module unavailable) is reported as
    ``verdict=False`` with explanation ``"checkpoint verification failed"``.
    """
    from app.utils import format_prompt

    start = time.monotonic()
    account_id = f"task-checkpoint:{uuid.uuid4()}"
    with app.app_context():
        try:
            from modules.base import response_language_name

            base = app.modules.get("base")
            if base is None or not hasattr(base, "call_llamacpp"):
                raise RuntimeError("base module (llamacpp client) is unavailable")
            params = {
                "step_goal": step.get("goal", ""),
                "step_checkpoint": step.get("checkpoint_criteria", ""),
                "step_result": (result or "")[:CHECKPOINT_RESULT_MAX_CHARS],
                "response_language": response_language_name(lang),
            }
            prompt = format_prompt("task_checkpoint.template", params, lang=lang)
            if not prompt:
                raise RuntimeError("task_checkpoint prompt template unavailable")
            begin_usage_account(account_id)
            verdict = _verify_with_language_guard(base, prompt, lang)
        except Exception as e:
            logger.error(f"run_checkpoint failed for step {step.get('id', '?')}: {e}")
            verdict = {"verdict": False, "explanation": "checkpoint verification failed"}
        finally:
            usage = finish_usage_account(account_id) or {}
        verdict["explanation"] = localize_checkpoint_explanation(str(verdict.get("explanation") or ""), lang)
        verdict["exec_seconds"] = round(time.monotonic() - start, 2)
        # The verifier's model + real token usage. For a step whose content came
        # from a tool (calculate/search) this is the ONLY model call made for it,
        # so the conversation header shows it there instead of a tool name.
        config = get_model_config("reasoning") or {}
        verdict["model_name"] = str(config.get("model_name") or "")
        totals = {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
        }
        # ``finish_usage_account`` also carries submitted_at — keep only the
        # real token totals, and report "no usage" as an empty dict.
        verdict["usage"] = totals if any(totals.values()) else {}
        return verdict


def run_media_checkpoint(
    app: Flask,
    step: dict[str, Any],
    *,
    expected_reference: str,
    generation_prompt: str,
    artifact_description: str,
    lang: str = "ru",
) -> dict[str, Any]:
    """Judge a produced artifact against the expected content and its generator prompt.

    The order is fixed (spec §10.3): first the artifact against the expected
    content resolved from the step's source, then against the instruction the
    generator actually received. Returns the same payload as ``run_checkpoint``
    (``verdict``/``explanation``/``exec_seconds``/``model_name``/``usage``).
    """
    from app.utils import format_prompt

    start = time.monotonic()
    account_id = f"task-media-checkpoint:{uuid.uuid4()}"
    with app.app_context():
        try:
            from modules.base import response_language_name

            base = app.modules.get("base")
            if base is None or not hasattr(base, "call_llamacpp"):
                raise RuntimeError("base module (llamacpp client) is unavailable")
            params = {
                "expected_reference": (expected_reference or "")[:MEDIA_REFERENCE_MAX_CHARS],
                "step_goal": str((step or {}).get("goal") or "")[:CHECKPOINT_RESULT_MAX_CHARS],
                "step_checkpoint": str((step or {}).get("checkpoint_criteria") or "")[:CHECKPOINT_RESULT_MAX_CHARS],
                "generation_prompt": (generation_prompt or "")[:CHECKPOINT_RESULT_MAX_CHARS],
                "artifact_description": (artifact_description or "")[:CHECKPOINT_RESULT_MAX_CHARS],
                "response_language": response_language_name(lang),
            }
            prompt = format_prompt("task_checkpoint_media.template", params, lang=lang)
            if not prompt:
                raise RuntimeError("task_checkpoint_media prompt template unavailable")
            begin_usage_account(account_id)
            verdict = _verify_with_language_guard(base, prompt, lang)
        except Exception as e:
            logger.error(f"run_media_checkpoint failed for step {step.get('id', '?')}: {e}")
            verdict = {"verdict": False, "explanation": "checkpoint verification failed"}
        finally:
            usage = finish_usage_account(account_id) or {}
        verdict["explanation"] = localize_checkpoint_explanation(str(verdict.get("explanation") or ""), lang)
        verdict["exec_seconds"] = round(time.monotonic() - start, 2)
        config = get_model_config("reasoning") or {}
        verdict["model_name"] = str(config.get("model_name") or "")
        totals = {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
        }
        verdict["usage"] = totals if any(totals.values()) else {}
        return verdict
