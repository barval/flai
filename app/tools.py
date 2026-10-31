# app/tools.py
"""Tool Calling registry and executors for FLAI.

Provides OpenAI-compatible tool definitions for llama.cpp and safe executors.
Tools are available to the chat model for direct responses.
"""

from __future__ import annotations

import ast
import json
import logging
import math
import operator
from typing import Any

import pendulum
from flask_babel import force_locale
from flask_babel import gettext as _

from app.mcp_client import NAMESPACE_PREFIX

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5

# One tool result larger than the window destroys an 8k conversation. The bound
# is character-based, not line-based, because a minified page is a single line.
TOOL_OUTPUT_MAX_CHARS = 4000
TOOL_OUTPUT_HEAD_SHARE = 0.6
# Room for the truncation marker, whose only variable part is the digit count.
TOOL_OUTPUT_MARKER_RESERVE = 50
MAX_EXPRESSION_LENGTH = 200

# ── Safe eval for calculator ─────────────────────────────────────────

_SAFE_BINOPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}

_SAFE_UNARYOPS: dict[type, Any] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_SAFE_CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
    "nan": math.nan,
}

_SAFE_FUNCTIONS: dict[str, Any] = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "exp": math.exp,
    "ceil": math.ceil,
    "floor": math.floor,
    "pow": pow,
}


def _eval_node(node: ast.AST) -> Any:
    """Recursively evaluate an AST node using only safe operations."""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, complex)):
            return node.value
        raise ValueError(f"Unsupported constant type: {type(node.value).__name__}")
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _SAFE_BINOPS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        return _SAFE_BINOPS[op_type](left, right)
    if isinstance(node, ast.UnaryOp):
        unary_op_type = type(node.op)
        if unary_op_type not in _SAFE_UNARYOPS:
            raise ValueError(f"Unsupported unary operator: {unary_op_type.__name__}")
        operand = _eval_node(node.operand)
        return _SAFE_UNARYOPS[unary_op_type](operand)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only direct function calls are allowed")
        func_name = node.func.id
        if func_name not in _SAFE_FUNCTIONS:
            raise ValueError(f"Unknown function: {func_name}")
        args = [_eval_node(arg) for arg in node.args]
        kwargs = {str(kw.arg): _eval_node(kw.value) for kw in node.keywords}
        return _SAFE_FUNCTIONS[func_name](*args, **kwargs)
    if isinstance(node, ast.Name):
        if node.id in _SAFE_CONSTANTS:
            return _SAFE_CONSTANTS[node.id]
        raise ValueError(f"Unknown variable: {node.id}")
    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(elt) for elt in node.elts)
    if isinstance(node, ast.List):
        return [_eval_node(elt) for elt in node.elts]
    raise ValueError(f"Unsupported expression: {type(node).__name__}")


def safe_eval(expression: str) -> float | int | tuple | list:
    """Evaluate a math expression safely using AST parsing.

    Only arithmetic operations, math functions, and constants are allowed.
    No imports, no assignments, no side effects.
    """
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ValueError(f"Expression too long (max {MAX_EXPRESSION_LENGTH} characters)")
    expression = expression.strip()
    if not expression:
        raise ValueError("Empty expression")
    tree = ast.parse(expression, mode="eval")
    result = _eval_node(tree.body)
    return result  # type: ignore[no-any-return]


# ── Tool definitions (OpenAI format for llama.cpp) ───────────────────

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get the current date and time. Use this when the user asks about the current time, date, day of the week, or any temporal information.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Perform a precise mathematical calculation. Use this for any arithmetic, math operations, or when the user asks to calculate something. Supports: +, -, *, /, **, %, sqrt, sin, cos, tan, log, abs, round, min, max, pi, e.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Mathematical expression to evaluate, e.g. '2 + 2', 'sqrt(144)', '15 * 37 + 128'",
                    },
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the internet for up-to-date information: news, weather, exchange rates, prices, latest events, releases, statistics. Use when the user needs fresh information that is not in their documents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "lang": {
                        "type": "string",
                        "description": "Language for search results (ru/en)",
                        "default": "ru",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_page",
            "description": "Open and read a specific web page (URL) with a real browser and return its text content. Use when the user gives a link and asks what is on it or to analyze it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full page URL, starting with http(s)://"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_search",
            "description": "Search for information in the user's uploaded documents. Use when the user asks about their files, documents, people, biography, addresses, dates, or facts that might be stored in their documents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query within documents",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default 5)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "history_search",
            "description": "Search the user's past conversation history. Use when the user asks about things said, discussed, decided, or mentioned in earlier chats ('when did we talk about...', 'what did I say about...', 'find in our chats...').",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query: the words that must appear in past messages",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of fragments to return (default 5)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "camera_snapshot",
            "description": (
                "Take a fresh snapshot of ONE room and return a DESCRIPTION of what is visible in it "
                "(people, animals, objects, activity). Use it to answer a question about a room's current "
                "state; call it once per room when the question needs several rooms compared."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "room": {
                        "type": "string",
                        "description": "Room code or name (e.g. 'gos', 'kab', 'living room')",
                    },
                },
                "required": ["room"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_rooms",
            "description": (
                "List the camera rooms this user may look at, with their codes and names. "
                "Call it before checking several rooms so every camera_snapshot call names a real room."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "time_calc",
            "description": (
                "Date and time calculations. Use for ANY question about dates, days, weekdays.\n"
                "OPERATIONS:\n"
                "- days_until_weekday: days until next weekday. Examples: 'сколько дней до понедельника' → days_until_weekday(weekday='понедельник'). 'сколько дней до ближайшей пятницы' → days_until_weekday(weekday='пятница'). 'когда следующая среда' → days_until_weekday(weekday='среда')\n"
                "- days_until_date: days from today until a specific date. Example: 'сколько дней до 30 июня' → days_until_date(date='2026-06-30')\n"
                "- days_until_end_of: days from today until the end of a named period. Examples: 'сколько дней до конца года' → days_until_end_of(period='year'). 'сколько дней до конца квартала' → days_until_end_of(period='quarter'). 'сколько дней до конца лета' → days_until_end_of(period='summer'). 'сколько дней до конца зимы' → days_until_end_of(period='winter')\n"
                "- days_since_end_of: days since the end of a named period (past direction). Examples: 'сколько дней назад закончилась весна' → days_since_end_of(period='spring'). 'сколько дней назад закончилось лето' → days_since_end_of(period='summer')\n"
                "- find_next_weekday_on_day: find the next date that is both a specific weekday AND a specific day of month. Example: 'какой ближайший четверг на 30 число' → find_next_weekday_on_day(weekday='четверг', day=30)\n"
                "- day_of_week: what day of week is a date. Example: 'какой день недели 12 июня' → day_of_week(date='2026-06-12')\n"
                "- days_between: days between two dates. Example: 'сколько дней между 11 и 15 июня' → days_between(from_date='2026-06-11', to_date='2026-06-15')\n"
                "- add_days: add days to date. Example: 'какая дата через 5 дней' → add_days(date='today', days=5)\n"
                "- format_date: format a date. Example: 'какое сегодня число' → format_date(date='today')"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "description": "Operation to perform",
                        "enum": [
                            "days_until_weekday",
                            "days_until_date",
                            "days_until_end_of",
                            "days_since_end_of",
                            "find_next_weekday_on_day",
                            "day_of_week",
                            "days_between",
                            "add_days",
                            "format_date",
                        ],
                    },
                    "weekday": {
                        "type": "string",
                        "description": "Day name in Russian or English: 'понедельник'/'monday', 'вторник'/'tuesday', 'среда'/'wednesday', 'четверг'/'thursday', 'пятница'/'friday', 'суббота'/'saturday', 'воскресенье'/'sunday'",
                    },
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format, or 'today'",
                    },
                    "from_date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format, or 'today'",
                    },
                    "to_date": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format, or 'today'",
                    },
                    "days": {
                        "type": "integer",
                        "description": "Number of days to add",
                    },
                    "day": {
                        "type": "integer",
                        "description": "Day of month (1-31) for find_next_weekday_on_day",
                    },
                    "period": {
                        "type": "string",
                        "description": (
                            "Named period: 'year'/'год', 'half'/'полугодие', 'quarter'/'квартал', "
                            "'month'/'месяц', 'spring'/'весна', 'summer'/'лето', 'autumn'/'осень', 'winter'/'зима'. "
                            "Seasons use meteorological dates: spring ends May 31, summer ends Aug 31, "
                            "autumn ends Nov 30, winter ends Feb 28/29. "
                            "Quarters: Q1 (Jan-Mar), Q2 (Apr-Jun), Q3 (Jul-Sep), Q4 (Oct-Dec). "
                            "Half-years: H1/H1 (Jan-Jun), H2/2H (Jul-Dec). "
                            "Weeks: W1-W52 (week of year)"
                        ),
                    },
                    "format": {
                        "type": "string",
                        "description": "Output format: 'long' (11 июня 2026, четверг), 'short' (11.06.2026), 'iso' (2026-06-11)",
                        "enum": ["long", "short", "iso"],
                        "default": "long",
                    },
                },
                "required": ["operation"],
            },
        },
    },
]


def _camera_available() -> bool:
    """Is the camera module registered and reachable? Unknown counts as no."""
    try:
        from flask import current_app

        cam = current_app.modules.get("cam")
    except Exception:  # noqa: BLE001 — no app context means not configured
        return False
    return bool(cam is not None and getattr(cam, "available", False))


def get_tool_definitions(lang: str = "ru") -> list[dict[str, Any]]:
    """Return tool definitions in OpenAI format for llama.cpp.

    ``read_page`` is only offered to the model when the crawler module is
    registered and available; otherwise the returned list excludes it.

    Operator-configured MCP tools are appended. A registry failure contributes
    nothing: losing an external tool must not cost the user their answer.
    """
    if get_crawler_module() is None:
        definitions = [d for d in TOOL_DEFINITIONS if d["function"]["name"] != "read_page"]
    else:
        definitions = list(TOOL_DEFINITIONS)
    if not _camera_available():
        # A tool that can only answer "the service is unavailable" is noise in
        # the catalogue; the camera module is optional.
        definitions = [d for d in definitions if d["function"]["name"] != "list_rooms"]
    return definitions + skill_tool_definitions() + mcp_tool_definitions()


# A restricted profile's catalogue, named here rather than in the module that
# runs it: "which tools may a research step reach" is a property of the tool
# catalogue, and a tuple living in the runner was the same drift the capability
# manifest put an end to. A role absent from this map is unrestricted (chat).
ROLE_TOOLS: dict[str, frozenset[str]] = {
    "research": frozenset(
        {
            "web_search",
            "read_page",
            "rag_search",
            "history_search",
            "get_current_time",
        }
    ),
}


def definitions_for_role(role: str, lang: str = "ru") -> list[dict[str, Any]]:
    """The catalogue one profile may see.

    Pruning is physical: a tool outside the role's set is absent from the list
    the model is given, so it cannot call what it was never shown. An unknown
    role means the full catalogue — the restricted roles are the exception, and
    a typo in a role name must not silently empty a profile's tool list.
    """
    definitions = get_tool_definitions(lang)
    allowed = ROLE_TOOLS.get(role)
    if allowed is None:
        return definitions
    return [item for item in definitions if item["function"]["name"] in allowed]


def skill_tool_definitions() -> list[dict[str, Any]]:
    """The activation tool, or nothing when no skill is installed."""
    try:
        from app.skills import skill_tool_definition

        definition = skill_tool_definition()
    except Exception:  # noqa: BLE001 - a broken skills folder must not fail the request
        logger.warning("Skill discovery failed; the activation tool is not offered", exc_info=True)
        return []
    return [definition] if definition else []


def mcp_tool_definitions() -> list[dict[str, Any]]:
    """The advertised MCP tools, or nothing when the feature is off or broken."""
    try:
        from flask import current_app

        if not current_app.config.get("MCP_ENABLED", False):
            return []
    except Exception:  # noqa: BLE001 - no app context means not configured
        return []
    try:
        from app.mcp_registry import tool_definitions

        return tool_definitions()
    except Exception:  # noqa: BLE001 - a down registry must not fail the request
        logger.warning("MCP tool enumeration failed; the MCP tools are not offered", exc_info=True)
        return []


# ── Tool executors ───────────────────────────────────────────────────


def get_crawler_module() -> Any:
    """Return the crawler module when it is enabled and reachable, else None."""
    from flask import current_app, has_app_context

    if not has_app_context():
        return None
    app = current_app._get_current_object()  # type: ignore[attr-defined]  # noqa: SLF001 — tools run inside app context
    module = app.modules.get("crawler")  # type: ignore[attr-defined]
    if module and module.available:
        return module
    return None


def _exec_get_current_time(ctx: dict[str, Any]) -> str:
    """Get current time using the app's timezone settings."""
    from app.utils import get_current_time_in_timezone

    app = ctx.get("app")
    time_str = get_current_time_in_timezone(app)
    if time_str:
        return time_str
    lang = ctx.get("lang", "ru")
    with force_locale(lang):
        return str(_("Current time unavailable"))


def _exec_calculator(ctx: dict[str, Any], expression: str) -> str:
    """Evaluate a math expression safely."""
    try:
        result = safe_eval(expression)
        if isinstance(result, float):
            if result == int(result) and not math.isinf(result):
                return str(int(result))
            return f"{result:.10g}"
        return str(result)
    except ZeroDivisionError:
        lang = ctx.get("lang", "ru")
        with force_locale(lang):
            return str(_("Division by zero"))
    except (ValueError, SyntaxError) as e:
        # A parse message is about the user's own expression and carries nothing
        # from the environment, so it is more useful to the model than a generic
        # sentence. Unexpected exceptions take the path below instead.
        lang = ctx.get("lang", "ru")
        with force_locale(lang):
            return str(_("Calculation error: {error}").format(error=str(e)))
    except Exception:
        logger.exception("calculator failed")
        lang = ctx.get("lang", "ru")
        with force_locale(lang):
            return str(_("The tool could not complete the request."))


def _exec_web_search(ctx: dict[str, Any], query: str, lang: str = "ru") -> str:
    """Search the web via Tavily (when the user has a key) or SearXNG."""
    from app.tavily_keys import get_tavily_key

    app = ctx.get("app")
    search_module = app.modules.get("search") if app else None
    user_id = ctx.get("user_id")
    api_key = get_tavily_key(user_id) if isinstance(user_id, str) and user_id else None
    if not search_module or (not search_module.available and not api_key):
        with force_locale(lang):
            return str(_("Web search service unavailable"))

    max_results = app.config.get("SEARXNG_MAX_RESULTS", 7) if app else 7
    results, _provider = search_module.search_with_fallback(
        query, lang=lang, max_results=max_results, api_key=api_key, user_id=user_id
    )
    if not results:
        with force_locale(lang):
            return str(_("No results found for query: {query}").format(query=query))

    base = app.modules.get("base") if app else None
    search_max_chars = base.get_search_context_limit() if base and hasattr(base, "get_search_context_limit") else 7000
    formatted = search_module.format_results_context(results, lang=lang, max_chars=search_max_chars)
    return formatted  # type: ignore[no-any-return]


def _exec_read_page(ctx: dict[str, Any], url: str) -> str:
    """Read one web page through the crawler container."""
    from app.crawler_guard import BlockedUrlError, validate_url

    lang = ctx.get("lang", "ru")
    crawler = get_crawler_module()
    if not crawler:
        with force_locale(lang):
            return str(_("Page reading service is unavailable"))
    try:
        validate_url(url)
    except BlockedUrlError:
        with force_locale(lang):
            return str(_("This address is not available for reading"))
    try:
        return str(crawler.read_page(url, user_id=ctx.get("user_id")))
    except Exception:
        logger.error(f"read_page tool failed for {url[:256]}")
        with force_locale(lang):
            return str(_("Failed to read the page"))


def _exec_rag_search(ctx: dict[str, Any], query: str, top_k: int = 5) -> str:
    """Search user's documents via RAG module."""
    app = ctx.get("app")
    user_id = ctx.get("user_id")
    lang = ctx.get("lang", "ru")
    rag_module = app.modules.get("rag") if app else None
    if not rag_module or not rag_module.available:
        with force_locale(lang):
            return str(_("Document search service unavailable"))
    if not user_id:
        with force_locale(lang):
            return str(_("User not identified for document search"))

    try:
        chunks, _scores = rag_module.search(user_id, query, top_k=top_k)
    except Exception:
        logger.exception("RAG search tool failed")
        with force_locale(lang):
            return str(_("The tool could not complete the request."))

    if not chunks:
        with force_locale(lang):
            return str(_("No relevant documents found for query: {query}").format(query=query))

    parts = []
    for i, chunk in enumerate(chunks):
        text = chunk.get("text", "")
        filename = chunk.get("filename", "")
        parts.append(f"[{i + 1}. {filename}]\n{text}")

    result = "\n\n".join(parts)
    max_rag_chars = app.config.get("RAG_MAX_RESULTS_CHARS", 5000) if app else 5000
    if len(result) > max_rag_chars:
        result = result[:max_rag_chars] + "..."
    return result


def _exec_history_search(ctx: dict[str, Any], query: str, limit: int = 5) -> str:
    """Search the user's past conversations via lexical history search."""
    app = ctx.get("app")
    user_id = ctx.get("user_id")
    lang = ctx.get("lang", "ru")
    if not user_id:
        with force_locale(lang):
            return str(_("User not identified for conversation search"))

    from modules.history import format_history_context, search_history

    try:
        fragments = search_history(
            user_id,
            query,
            limit=limit,
            exclude_message_id=ctx.get("current_message_id"),
            exclude_session_id=ctx.get("current_session_id"),
            max_message_chars=app.config.get("HISTORY_MAX_MESSAGE_CHARS", 20000) if app else 20000,
        )
    except Exception:
        logger.exception("History search tool failed")
        with force_locale(lang):
            return str(_("The tool could not complete the request."))

    if not fragments:
        with force_locale(lang):
            return str(_("No past conversations found for query: {query}").format(query=query))

    max_chars = app.config.get("HISTORY_MAX_RESULTS_CHARS", 5000) if app else 5000
    return format_history_context(fragments, max_chars=max_chars)


def _exec_camera_snapshot(ctx: dict[str, Any], room: str) -> dict[str, Any]:
    """Get a snapshot from a camera. Returns dict with image_data or error."""
    app = ctx.get("app")
    user_id = ctx.get("user_id")
    lang = ctx.get("lang", "ru")
    cam_module = app.modules.get("cam") if app else None
    if not cam_module or not cam_module.available:
        with force_locale(lang):
            return {"success": False, "error": _("CCTV service unavailable")}

    result = cam_module.get_snapshot(user_id, room, lang=lang)
    if not result.get("success"):
        return result  # type: ignore[no-any-return]

    # A tool result reaches the model as TEXT: the base64 image would burn the
    # window and tell it nothing. The snapshot is described by the multimodal
    # model instead, which is the answer the tool loop needs ("is a cat there?").
    image_data = result.get("image_data") or ""
    multimodal = app.modules.get("multimodal") if app else None
    if image_data and multimodal is not None and getattr(multimodal, "available", False):
        with force_locale(lang):
            question = str(_("What is visible in this room right now? Name people, animals, objects and activity."))
        description, error = multimodal.process_image_with_text(
            image_data, question, "", lang=lang, session_id=None, response_style="neutral"
        )
        if error or not description:
            return {"success": False, "error": str(error or "description failed")}
        return {"success": True, "room": result.get("room_name", room), "description": description}
    return {
        "success": True,
        "room": result.get("room_name", room),
        "description": str(_("Snapshot captured; no description is available.")),
    }


def _exec_list_rooms(ctx: dict[str, Any]) -> dict[str, Any]:
    """The rooms this user may look at, code and localized name."""
    app = ctx.get("app")
    user_id = ctx.get("user_id")
    lang = ctx.get("lang", "ru")
    cam_module = app.modules.get("cam") if app else None
    if not cam_module or not cam_module.available:
        with force_locale(lang):
            return {"success": False, "error": _("CCTV service unavailable")}
    rooms = cam_module.get_available_rooms(user_id) or {}
    return {
        "success": True,
        "rooms": [{"code": code, "name": cam_module.get_room_name(code, lang=lang)} for code in sorted(rooms)],
    }


# ── Time calculation helpers ────────────────────────────────────────

_WEEKDAY_MAP_RU = {
    "понедельник": 0,
    "вторник": 1,
    "среда": 2,
    "четверг": 3,
    "пятница": 4,
    "суббота": 5,
    "воскресенье": 6,
    "пн": 0,
    "вт": 1,
    "ср": 2,
    "чт": 3,
    "пт": 4,
    "сб": 5,
    "вс": 6,
}
_WEEKDAY_MAP_EN = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}
_WEEKDAY_NAMES_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_WEEKDAY_NAMES_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _parse_date_param(date_str: str, tz: str = "Europe/Moscow") -> pendulum.DateTime:
    """Parse a date parameter, supporting 'today' keyword."""
    import pendulum

    if not date_str or date_str.lower() == "today":
        return pendulum.now(tz).start_of("day")
    return pendulum.parse(date_str, exact=True)  # type: ignore[return-value]


def _format_date_result(dt: pendulum.DateTime, fmt: str, lang: str = "ru") -> str:
    """Format a date for display."""
    if fmt == "iso":
        return dt.to_date_string()
    if fmt == "short":
        return dt.format("DD.MM.YYYY")
    # long format
    if lang == "ru":
        return dt.format("D MMMM YYYY, dddd", locale="ru")
    return dt.format("MMMM D, YYYY, dddd", locale="en")


def _exec_time_calc(
    ctx: dict[str, Any],
    operation: str,
    weekday: str = "",
    date: str = "",
    from_date: str = "",
    to_date: str = "",
    days: int = 0,
    day: int = 0,
    period: str = "",
    format: str = "long",
) -> str:
    """Execute date/time calculations using Pendulum."""
    lang = ctx.get("lang", "ru")
    tz = "Europe/Moscow"

    try:
        if operation == "days_until_weekday":
            if not weekday:
                with force_locale(lang):
                    return str(_("Date calculation error: {error}").format(error="weekday is required"))
            weekday_lower = weekday.lower()
            weekday_num = _WEEKDAY_MAP_RU.get(weekday_lower)
            if weekday_num is None:
                weekday_num = _WEEKDAY_MAP_EN.get(weekday_lower)
            if weekday_num is None:
                with force_locale(lang):
                    return str(_("Date calculation error: {error}").format(error=f"unknown weekday: {weekday}"))
            d = _parse_date_param(date, tz)
            days_ahead = (weekday_num - d.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return str(days_ahead)

        elif operation == "days_until_date":
            d = _parse_date_param(date, tz)
            today = pendulum.now(tz).start_of("day")
            diff = (d - today).in_days()
            return str(diff)

        elif operation == "days_until_end_of":
            if not period:
                with force_locale(lang):
                    return str(_("Date calculation error: {error}").format(error="period is required"))
            now = pendulum.now(tz)
            today = now.start_of("day")
            year, month = now.year, now.month

            if period in ("month", "месяц"):
                end = now.end_of("month")
            elif period in ("quarter", "квартал"):
                q = (month - 1) // 3
                q_end_month = q * 3 + 3
                end = pendulum.datetime(year, q_end_month, 28, tz=tz).end_of("month")
            elif period in ("Q1", "q1"):
                end = pendulum.datetime(year, 3, 31, tz=tz)
            elif period in ("Q2", "q2"):
                end = pendulum.datetime(year, 6, 30, tz=tz)
            elif period in ("Q3", "q3"):
                end = pendulum.datetime(year, 9, 30, tz=tz)
            elif period in ("Q4", "q4"):
                end = pendulum.datetime(year, 12, 31, tz=tz)
            elif period in ("H1", "h1", "1H", "1h"):
                end = pendulum.datetime(year, 6, 30, tz=tz)
            elif period in ("H2", "h2", "2H", "2h"):
                end = pendulum.datetime(year, 12, 31, tz=tz)
            elif period in ("half", "полугодие"):
                end = pendulum.datetime(year, 6, 30, tz=tz) if month <= 6 else pendulum.datetime(year, 12, 31, tz=tz)  # noqa: SIM108
            elif period in ("year", "год"):
                end = pendulum.datetime(year, 12, 31, tz=tz)
            elif period in ("spring", "весна"):
                end = pendulum.datetime(year, 5, 31, tz=tz)
            elif period in ("summer", "лето"):
                end = pendulum.datetime(year, 8, 31, tz=tz)
            elif period in ("autumn", "осень"):
                end = pendulum.datetime(year, 11, 30, tz=tz)
            elif period in ("winter", "зима"):
                end = pendulum.datetime(year + 1, 2, 28, tz=tz)
            elif period.upper().startswith("W"):
                try:
                    week_num = int(period[1:])
                    if week_num < 1 or week_num > 52:
                        raise ValueError
                    # ISO week 1 is the week containing Jan 4
                    jan4 = pendulum.datetime(year, 1, 4, tz=tz)
                    week1_start = jan4.start_of("week")
                    target_start = week1_start.add(weeks=week_num - 1)
                    end = target_start.add(days=6)
                except (ValueError, IndexError):
                    with force_locale(lang):
                        return str(_("Date calculation error: {error}").format(error=f"invalid week number: {period}"))
            else:
                with force_locale(lang):
                    return str(_("Date calculation error: {error}").format(error=f"unknown period: {period}"))

            diff = (end - today).in_days()
            return str(diff)

        elif operation == "days_since_end_of":
            if not period:
                with force_locale(lang):
                    return str(_("Date calculation error: {error}").format(error="period is required"))
            now = pendulum.now(tz)
            today = now.start_of("day")
            year, month = now.year, now.month

            if period in ("month", "месяц"):
                end = now.end_of("month")
            elif period in ("quarter", "квартал"):
                q = (month - 1) // 3
                q_end_month = q * 3 + 3
                end = pendulum.datetime(year, q_end_month, 28, tz=tz).end_of("month")
            elif period in ("Q1", "q1"):
                end = pendulum.datetime(year, 3, 31, tz=tz)
            elif period in ("Q2", "q2"):
                end = pendulum.datetime(year, 6, 30, tz=tz)
            elif period in ("Q3", "q3"):
                end = pendulum.datetime(year, 9, 30, tz=tz)
            elif period in ("Q4", "q4"):
                end = pendulum.datetime(year, 12, 31, tz=tz)
            elif period in ("H1", "h1", "1H", "1h"):
                end = pendulum.datetime(year, 6, 30, tz=tz)
            elif period in ("H2", "h2", "2H", "2h"):
                end = pendulum.datetime(year, 12, 31, tz=tz)
            elif period in ("half", "полугодие"):
                end = pendulum.datetime(year, 6, 30, tz=tz) if month <= 6 else pendulum.datetime(year, 12, 31, tz=tz)  # noqa: SIM108
            elif period in ("year", "год"):
                end = pendulum.datetime(year, 12, 31, tz=tz)
            elif period in ("spring", "весна"):
                end = pendulum.datetime(year, 5, 31, tz=tz)
            elif period in ("summer", "лето"):
                end = pendulum.datetime(year, 8, 31, tz=tz)
            elif period in ("autumn", "осень"):
                end = pendulum.datetime(year, 11, 30, tz=tz)
            elif period in ("winter", "зима"):
                end = pendulum.datetime(year + 1, 2, 28, tz=tz)
            elif period.upper().startswith("W"):
                try:
                    week_num = int(period[1:])
                    if week_num < 1 or week_num > 52:
                        raise ValueError
                    jan4 = pendulum.datetime(year, 1, 4, tz=tz)
                    week1_start = jan4.start_of("week")
                    target_start = week1_start.add(weeks=week_num - 1)
                    end = target_start.add(days=6)
                except (ValueError, IndexError):
                    with force_locale(lang):
                        return str(_("Date calculation error: {error}").format(error=f"invalid week number: {period}"))
            else:
                with force_locale(lang):
                    return str(_("Date calculation error: {error}").format(error=f"unknown period: {period}"))

            diff = (today - end).in_days()
            return str(diff)

        elif operation == "find_next_weekday_on_day":
            if not weekday or not day:
                with force_locale(lang):
                    return str(_("Date calculation error: {error}").format(error="weekday and day are required"))
            weekday_lower = weekday.lower()
            weekday_num = _WEEKDAY_MAP_RU.get(weekday_lower)
            if weekday_num is None:
                weekday_num = _WEEKDAY_MAP_EN.get(weekday_lower)
            if weekday_num is None:
                with force_locale(lang):
                    return str(_("Date calculation error: {error}").format(error=f"unknown weekday: {weekday}"))
            now = pendulum.now(tz).start_of("day")
            current = now
            for _day_offset in range(366):
                if current.day == day and current.weekday() == weekday_num:
                    if lang == "ru":
                        return current.format("D MMMM YYYY, dddd", locale="ru")
                    return current.format("MMMM D, YYYY, dddd", locale="en")
                current = current.add(days=1)
            with force_locale(lang):
                return str(_("Date calculation error: {error}").format(error="no matching date found in next year"))

        elif operation == "day_of_week":
            d = _parse_date_param(date, tz)
            if lang == "ru":
                return _WEEKDAY_NAMES_RU[d.weekday()]
            return _WEEKDAY_NAMES_EN[d.weekday()]

        elif operation == "days_between":
            d1 = _parse_date_param(from_date or date, tz)
            d2 = _parse_date_param(to_date, tz)
            diff = (d2 - d1).in_days()
            return str(diff)

        elif operation == "add_days":
            d = _parse_date_param(date, tz)
            result = d.add(days=days)
            return _format_date_result(result, format, lang)

        elif operation == "format_date":
            d = _parse_date_param(date, tz)
            return _format_date_result(d, format, lang)

        else:
            with force_locale(lang):
                return str(_("Unknown time_calc operation: {op}").format(op=operation))

    except ValueError as e:
        with force_locale(lang):
            return str(_("Date calculation error: {error}").format(error=str(e)))
    except Exception:
        logger.exception("time_calc failed")
        with force_locale(lang):
            return str(_("The tool could not complete the request."))


# ── Tool dispatch ────────────────────────────────────────────────────

_EXECUTOR_MAP: dict[str, Any] = {
    "get_current_time": lambda ctx, **kw: _exec_get_current_time(ctx),
    "calculator": lambda ctx, **kw: _exec_calculator(ctx, kw.get("expression", "")),
    "web_search": lambda ctx, **kw: _exec_web_search(ctx, kw.get("query", ""), kw.get("lang", "ru")),
    "read_page": lambda ctx, **kw: _exec_read_page(ctx, kw.get("url", "")),
    "rag_search": lambda ctx, **kw: _exec_rag_search(ctx, kw.get("query", ""), kw.get("top_k", 5)),
    "history_search": lambda ctx, **kw: _exec_history_search(ctx, kw.get("query", ""), kw.get("limit", 5)),
    "camera_snapshot": lambda ctx, **kw: _exec_camera_snapshot(ctx, kw.get("room", "")),
    "list_rooms": lambda ctx, **kw: _exec_list_rooms(ctx),
    "time_calc": lambda ctx, **kw: _exec_time_calc(ctx, **kw),
    "activate_skill": lambda ctx, **kw: _exec_activate_skill(kw.get("name", ""), kw.get("lang", "ru")),
}


SKILL_LOADER_TOOL = "activate_skill"


def _exec_activate_skill(name: str, lang: str) -> str:
    """Return a skill's body, or say which name is not in the catalog."""
    from app.skills import activate

    body = activate(name, lang)
    if body is None:
        with force_locale(lang):
            return str(_("Unknown skill: {name}").format(name=name))
    return body


def select_tool_definitions(selected: tuple[str, ...] | None, lang: str = "ru") -> list[dict[str, Any]]:
    """Advertise only the tools the request kernel chose.

    ``None`` and an empty tuple both mean "the kernel had no opinion" and return
    every tool. Measured on Qwen3VL-8B: asked which tools a message needs, the
    model answers ``[]`` for "what time is it?" even when the prompt names
    ``get_current_time`` explicitly. An empty list is therefore the one answer
    that is confidently wrong, and it is also the one that removes every
    capability at once, so the runtime may narrow the list but never empty it. A
    non-empty selection that matches nothing is treated the same way.

    Full tool schemas are the largest fixed cost of a chat request (about 2 000
    tokens for eight tools), so a request that needs none of them pays for none.
    """
    definitions = get_tool_definitions(lang)
    if not selected:
        return definitions
    wanted = set(selected)
    chosen = [item for item in definitions if item.get("function", {}).get("name") in wanted]
    if not chosen:
        return definitions
    if not any(item["function"]["name"] == SKILL_LOADER_TOOL for item in chosen):
        # The loader survives narrowing: a turn that picked any other tool would
        # otherwise lose the only way to load a skill for the rest of the turn.
        chosen += [item for item in definitions if item["function"]["name"] == SKILL_LOADER_TOOL]
    return chosen


def _coerce_value(value: Any, wanted: str) -> Any:
    """Coerce one value toward the schema's type, or return it unchanged.

    Anything ambiguous is left alone: a wrong coercion is worse than a failed
    call, because it makes the tool answer a question nobody asked.
    """
    if wanted == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "false"):
            return value.strip().lower() == "true"
        return value
    if wanted in ("integer", "number"):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                return int(value.strip()) if wanted == "integer" else float(value.strip())
            except ValueError:
                return value
        return value
    if wanted == "object" and isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        return parsed if isinstance(parsed, dict) else value
    if wanted == "array" and isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        return parsed if isinstance(parsed, list) else value
    return value


def repair_tool_arguments(arguments: dict[str, Any], schema: dict[str, Any] | None) -> dict[str, Any]:
    """Bring arguments closer to the shape the tool's schema declares.

    A small model sends ``"20"`` for an integer, ``"false"`` for a boolean, a
    stringified object for an object, and ``null`` placeholders for fields it
    left empty. All three are silent failures otherwise: the executor raises on
    a string comparison or the tool answers a different question.
    """
    if not schema:
        return arguments
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or ())
    repaired: dict[str, Any] = {}
    for key, value in arguments.items():
        declared = properties.get(key) or {}
        wanted = declared.get("type")
        if value is None and key not in required:
            continue
        if isinstance(wanted, str):
            value = _coerce_value(value, wanted)
        repaired[key] = value
    return repaired


def parse_tool_arguments(raw: Any, schema: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """Parse the argument string of a tool call.

    Returns ``(arguments, usable)``. ``usable`` is False when the string is not
    valid JSON at all: the caller must then reject the call instead of running
    the tool with empty arguments, which is how a search runs with an empty
    query and the model learns nothing from the failure.
    """
    if raw is None:
        # A null blob is the same shape as an unparsable one when the tool needs
        # fields: running it with nothing repeats the empty-query failure.
        return ({}, False) if (schema or {}).get("required") else ({}, True)
    if isinstance(raw, str) and not raw.strip():
        return {}, True
    if isinstance(raw, dict):
        return repair_tool_arguments(raw, schema), True
    if not isinstance(raw, str):
        return {}, False
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}, False
    if not isinstance(parsed, dict):
        return {}, False
    return repair_tool_arguments(parsed, schema), True


# Identical calls a model may make before the guard stops it. FLAI's own stream
# loop detector catches repeated *text*; this one catches a repeated *call*,
# which is the shape the failure takes once tools are involved.
TOOL_CALL_REPEAT_LIMIT = 3


class ToolCallLoopGuard:
    """Refuse a tool call that has already been made with the same arguments.

    One guard lives one conversation turn: a legitimate repeat in a later turn
    (a camera checked again after a question) must not be blocked by a count
    collected in an earlier one.
    """

    def __init__(self, repeats: int = TOOL_CALL_REPEAT_LIMIT):
        self.repeats = repeats
        self._seen: dict[tuple[str, str], int] = {}

    def check(self, name: str, arguments: dict[str, Any], schema: dict[str, Any] | None = None) -> bool:
        """Record the call and report whether it may run.

        When a schema is given only its required fields are compared: a model
        alternating an optional field would otherwise repeat the same call
        forever without ever tripping the limit.
        """
        required = (schema or {}).get("required") or ()
        if required:
            arguments = {key: arguments[key] for key in required if key in arguments}
        key = (name, json.dumps(arguments, sort_keys=True, default=str))
        count = self._seen.get(key, 0) + 1
        self._seen[key] = count
        return count < self.repeats


def cap_reached_message(lang: str = "ru") -> str:
    """What the stream reports when a tool call arrives after the last round."""
    with force_locale(lang):
        return str(_("The tool call was ignored: the last round of the loop has been reached."))


def tool_choice_for_iteration(iteration: int, max_iterations: int) -> str | None:
    """Withdraw the tools on the last round so the model must answer.

    Without it the loop runs out of iterations and shows the last raw tool
    result as the answer.
    """
    return "none" if iteration >= max_iterations - 1 else None


def repeated_call_message(lang: str = "ru") -> str:
    """What the model is told when it repeats a call it has already made."""
    with force_locale(lang):
        return str(_("The tool call was refused: it repeats a call made with the same arguments, whose result is above."))


def invalid_arguments_message(lang: str = "ru") -> str:
    """What the model is told when its tool call could not be parsed."""
    with force_locale(lang):
        return str(_("The tool call was rejected: its arguments were not valid JSON."))


def bound_tool_output(text: str, limit: int = TOOL_OUTPUT_MAX_CHARS) -> str:
    """Cap a tool result, keeping both ends and saying what was dropped.

    Both ends matter: a search result's first lines carry the sources and its
    last lines carry the conclusion. The marker is load-bearing — without it the
    model reads a clipped result as an empty one and calls the tool again.
    """
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    # The marker is charged against the limit, so the limit is a hard ceiling
    # rather than a target the marker then overshoots.
    budget = limit - TOOL_OUTPUT_MARKER_RESERVE
    if budget <= 0:
        return text[:limit]
    head = int(budget * TOOL_OUTPUT_HEAD_SHARE)
    tail = budget - head
    dropped = len(text) - budget
    return f"{text[:head]}\n…[truncated {dropped} characters]…\n{text[-tail:]}"


def execute_tool(tool_name: str, arguments: dict[str, Any], context: dict[str, Any]) -> str:
    """Execute a tool by name with given arguments.

    Args:
        tool_name: Name of the tool to execute.
        arguments: Tool arguments from the model.
        context: Execution context with 'app', 'user_id', 'lang'.

    Returns:
        Tool result as a string (or JSON-serializable dict for camera).
    """
    if tool_name.startswith(NAMESPACE_PREFIX):
        # A namespaced name belongs to an MCP server the operator configured; the
        # registry refuses anything that was not advertised, so an invented name
        # cannot reach a server the model was not offered.
        try:
            from app.mcp_registry import call as mcp_call

            return bound_tool_output(str(mcp_call(tool_name, arguments)))
        except Exception:  # noqa: BLE001 - a failed external tool is a message, not a crash
            logger.exception(f"MCP tool '{tool_name}' failed")
            lang = context.get("lang", "ru")
            with force_locale(lang):
                return str(_("The tool could not complete the request."))

    executor = _EXECUTOR_MAP.get(tool_name)
    if not executor:
        lang = context.get("lang", "ru")
        with force_locale(lang):
            return str(_("Unknown tool: {tool}").format(tool=tool_name))

    try:
        result = executor(context, **arguments)
        if isinstance(result, dict):
            result = json.dumps(result, ensure_ascii=False)
        return bound_tool_output(str(result))
    except Exception:
        # The detail stays in the log: a host name or a port echoed into a tool
        # result can travel straight into an answer.
        logger.exception(f"Tool '{tool_name}' execution failed")
        lang = context.get("lang", "ru")
        with force_locale(lang):
            return str(_("The tool could not complete the request."))
