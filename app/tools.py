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

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5
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
        op_type = type(node.op)
        if op_type not in _SAFE_UNARYOPS:
            raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
        operand = _eval_node(node.operand)
        return _SAFE_UNARYOPS[op_type](operand)
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Only direct function calls are allowed")
        func_name = node.func.id
        if func_name not in _SAFE_FUNCTIONS:
            raise ValueError(f"Unknown function: {func_name}")
        args = [_eval_node(arg) for arg in node.args]
        kwargs = {kw.arg: _eval_node(kw.value) for kw in node.keywords}
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
    return result


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
            "name": "camera_snapshot",
            "description": "Get a current snapshot from a surveillance camera. Use when the user asks to see, check, or look at a room/camera.",
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


def get_tool_definitions(lang: str = "ru") -> list[dict[str, Any]]:
    """Return tool definitions in OpenAI format for llama.cpp."""
    return TOOL_DEFINITIONS


# ── Tool executors ───────────────────────────────────────────────────

def _exec_get_current_time(ctx: dict[str, Any]) -> str:
    """Get current time using the app's timezone settings."""
    from app.utils import get_current_time_in_timezone

    app = ctx.get("app")
    time_str = get_current_time_in_timezone(app)
    if time_str:
        return time_str
    lang = ctx.get("lang", "ru")
    with force_locale(lang):
        return _("Current time unavailable")


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
            return _("Division by zero")
    except ValueError as e:
        lang = ctx.get("lang", "ru")
        with force_locale(lang):
            return _("Calculation error: {error}").format(error=str(e))
    except Exception as e:
        lang = ctx.get("lang", "ru")
        with force_locale(lang):
            return _("Calculation error: {error}").format(error=str(e))


def _exec_web_search(ctx: dict[str, Any], query: str, lang: str = "ru") -> str:
    """Search the web via SearXNG module."""
    app = ctx.get("app")
    search_module = app.modules.get("search") if app else None
    if not search_module or not search_module.available:
        with force_locale(lang):
            return _("Web search service unavailable")

    max_results = app.config.get("SEARXNG_MAX_RESULTS", 7) if app else 7
    results = search_module.search(query, lang=lang, max_results=max_results)
    if not results:
        with force_locale(lang):
            return _("No results found for query: {query}").format(query=query)

    formatted = search_module.format_results_context(results, lang=lang)
    max_chars = app.config.get("SEARXNG_MAX_RESULTS_CHARS", 7000) if app else 7000
    if len(formatted) > max_chars:
        formatted = formatted[:max_chars] + "..."
    return formatted


def _exec_rag_search(ctx: dict[str, Any], query: str, top_k: int = 5) -> str:
    """Search user's documents via RAG module."""
    app = ctx.get("app")
    user_id = ctx.get("user_id")
    lang = ctx.get("lang", "ru")
    rag_module = app.modules.get("rag") if app else None
    if not rag_module or not rag_module.available:
        with force_locale(lang):
            return _("Document search service unavailable")
    if not user_id:
        with force_locale(lang):
            return _("User not identified for document search")

    try:
        chunks, scores = rag_module.search(user_id, query, top_k=top_k)
    except Exception as e:
        logger.error(f"RAG search tool failed: {e}")
        with force_locale(lang):
            return _("Document search error: {error}").format(error=str(e))

    if not chunks:
        with force_locale(lang):
            return _("No relevant documents found for query: {query}").format(query=query)

    parts = []
    for i, chunk in enumerate(chunks):
        text = chunk.get("text", "")
        filename = chunk.get("filename", "")
        score = scores[i] if i < len(scores) else 0
        parts.append(f"[{i + 1}. {filename} (score: {score:.2f})]\n{text}")

    result = "\n\n".join(parts)
    max_rag_chars = app.config.get("RAG_MAX_RESULTS_CHARS", 5000) if app else 5000
    if len(result) > max_rag_chars:
        result = result[:max_rag_chars] + "..."
    return result


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
    return result


# ── Time calculation helpers ────────────────────────────────────────

_WEEKDAY_MAP_RU = {
    "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3,
    "пятница": 4, "суббота": 5, "воскресенье": 6,
    "пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6,
}
_WEEKDAY_MAP_EN = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}
_WEEKDAY_NAMES_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_WEEKDAY_NAMES_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _parse_date_param(date_str: str, tz: str = "Europe/Moscow") -> pendulum.DateTime:
    """Parse a date parameter, supporting 'today' keyword."""
    import pendulum

    if not date_str or date_str.lower() == "today":
        return pendulum.now(tz).start_of("day")
    return pendulum.parse(date_str, exact=True)


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
                    return _("Date calculation error: {error}").format(error="weekday is required")
            weekday_lower = weekday.lower()
            weekday_num = _WEEKDAY_MAP_RU.get(weekday_lower)
            if weekday_num is None:
                weekday_num = _WEEKDAY_MAP_EN.get(weekday_lower)
            if weekday_num is None:
                with force_locale(lang):
                    return _("Date calculation error: {error}").format(error=f"unknown weekday: {weekday}")
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
                    return _("Date calculation error: {error}").format(error="period is required")
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
                        return _("Date calculation error: {error}").format(error=f"invalid week number: {period}")
            else:
                with force_locale(lang):
                    return _("Date calculation error: {error}").format(error=f"unknown period: {period}")

            diff = (end - today).in_days()
            return str(diff)

        elif operation == "days_since_end_of":
            if not period:
                with force_locale(lang):
                    return _("Date calculation error: {error}").format(error="period is required")
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
                        return _("Date calculation error: {error}").format(error=f"invalid week number: {period}")
            else:
                with force_locale(lang):
                    return _("Date calculation error: {error}").format(error=f"unknown period: {period}")

            diff = (today - end).in_days()
            return str(diff)

        elif operation == "find_next_weekday_on_day":
            if not weekday or not day:
                with force_locale(lang):
                    return _("Date calculation error: {error}").format(error="weekday and day are required")
            weekday_lower = weekday.lower()
            weekday_num = _WEEKDAY_MAP_RU.get(weekday_lower)
            if weekday_num is None:
                weekday_num = _WEEKDAY_MAP_EN.get(weekday_lower)
            if weekday_num is None:
                with force_locale(lang):
                    return _("Date calculation error: {error}").format(error=f"unknown weekday: {weekday}")
            now = pendulum.now(tz).start_of("day")
            current = now
            for _day_offset in range(366):
                if current.day == day and current.weekday() == weekday_num:
                    if lang == "ru":
                        return current.format("D MMMM YYYY, dddd", locale="ru")
                    return current.format("MMMM D, YYYY, dddd", locale="en")
                current = current.add(days=1)
            with force_locale(lang):
                return _("Date calculation error: {error}").format(error="no matching date found in next year")

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
                return _("Unknown time_calc operation: {op}").format(op=operation)

    except ValueError as e:
        with force_locale(lang):
            return _("Date calculation error: {error}").format(error=str(e))
    except Exception as e:
        logger.error(f"time_calc failed: {e}")
        with force_locale(lang):
            return _("Date calculation error: {error}").format(error=str(e))


# ── Tool dispatch ────────────────────────────────────────────────────

_EXECUTOR_MAP: dict[str, Any] = {
    "get_current_time": lambda ctx, **kw: _exec_get_current_time(ctx),
    "calculator": lambda ctx, **kw: _exec_calculator(ctx, kw.get("expression", "")),
    "web_search": lambda ctx, **kw: _exec_web_search(ctx, kw.get("query", ""), kw.get("lang", "ru")),
    "rag_search": lambda ctx, **kw: _exec_rag_search(ctx, kw.get("query", ""), kw.get("top_k", 5)),
    "camera_snapshot": lambda ctx, **kw: _exec_camera_snapshot(ctx, kw.get("room", "")),
    "time_calc": lambda ctx, **kw: _exec_time_calc(ctx, **kw),
}


def execute_tool(tool_name: str, arguments: dict[str, Any], context: dict[str, Any]) -> str:
    """Execute a tool by name with given arguments.

    Args:
        tool_name: Name of the tool to execute.
        arguments: Tool arguments from the model.
        context: Execution context with 'app', 'user_id', 'lang'.

    Returns:
        Tool result as a string (or JSON-serializable dict for camera).
    """
    executor = _EXECUTOR_MAP.get(tool_name)
    if not executor:
        lang = context.get("lang", "ru")
        with force_locale(lang):
            return _("Unknown tool: {tool}").format(tool=tool_name)

    try:
        result = executor(context, **arguments)
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False)
        return str(result)
    except Exception as e:
        logger.error(f"Tool '{tool_name}' execution failed: {e}")
        lang = context.get("lang", "ru")
        with force_locale(lang):
            return _("Tool execution error: {error}").format(error=str(e))
