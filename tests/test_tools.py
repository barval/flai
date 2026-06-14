# tests/test_tools.py
"""Unit tests for app/tools.py — Tool Calling registry and executors."""

import math

import pytest

from app.tools import (
    TOOL_DEFINITIONS,
    execute_tool,
    get_tool_definitions,
    safe_eval,
)

# ── safe_eval tests ──────────────────────────────────────────────────


class TestSafeEvalBasic:
    """Basic arithmetic operations."""

    def test_addition(self):
        assert safe_eval("2 + 2") == 4

    def test_subtraction(self):
        assert safe_eval("10 - 3") == 7

    def test_multiplication(self):
        assert safe_eval("6 * 7") == 42

    def test_division(self):
        assert safe_eval("10 / 4") == 2.5

    def test_floor_division(self):
        assert safe_eval("10 // 3") == 3

    def test_modulo(self):
        assert safe_eval("10 % 3") == 1

    def test_power(self):
        assert safe_eval("2 ** 10") == 1024

    def test_unary_minus(self):
        assert safe_eval("-5") == -5

    def test_unary_plus(self):
        assert safe_eval("+5") == 5

    def test_complex_expression(self):
        assert safe_eval("15 * 37 + 128") == 683

    def test_parentheses(self):
        assert safe_eval("(2 + 3) * 4") == 20

    def test_nested_parentheses(self):
        assert safe_eval("((1 + 2) * (3 + 4))") == 21


class TestSafeEvalFunctions:
    """Math function support."""

    def test_sqrt(self):
        assert safe_eval("sqrt(144)") == 12

    def test_abs(self):
        assert safe_eval("abs(-42)") == 42

    def test_round(self):
        assert safe_eval("round(3.7)") == 4

    def test_min(self):
        assert safe_eval("min(1, 2, 3)") == 1

    def test_max(self):
        assert safe_eval("max(1, 2, 3)") == 3

    def test_sin(self):
        assert safe_eval("sin(0)") == 0

    def test_cos(self):
        assert safe_eval("cos(0)") == 1

    def test_log(self):
        assert safe_eval("log(e)") == 1

    def test_log10(self):
        assert safe_eval("log10(100)") == 2

    def test_exp(self):
        assert safe_eval("exp(0)") == 1

    def test_ceil(self):
        assert safe_eval("ceil(3.2)") == 4

    def test_floor(self):
        assert safe_eval("floor(3.8)") == 3

    def test_pow_function(self):
        assert safe_eval("pow(2, 10)") == 1024


class TestSafeEvalConstants:
    """Math constant support."""

    def test_pi(self):
        assert safe_eval("pi") == math.pi

    def test_e(self):
        assert safe_eval("e") == math.e

    def test_tau(self):
        assert safe_eval("tau") == math.tau

    def test_inf(self):
        assert safe_eval("inf") == math.inf


class TestSafeEvalRejection:
    """Security: rejected dangerous expressions."""

    def test_import_rejected(self):
        with pytest.raises(ValueError, match="Unknown function"):
            safe_eval("__import__('os')")

    def test_exec_rejected(self):
        with pytest.raises(ValueError, match="Unknown function"):
            safe_eval("exec('import os')")

    def test_eval_rejected(self):
        with pytest.raises(ValueError, match="Unknown function"):
            safe_eval("eval('1+1')")

    def test_open_rejected(self):
        with pytest.raises(ValueError, match="Unknown function"):
            safe_eval("open('/etc/passwd')")

    def test_class_access_rejected(self):
        with pytest.raises(ValueError):
            safe_eval("(1).__class__.__bases__[0].__subclasses__()")

    def test_division_by_zero(self):
        with pytest.raises(ZeroDivisionError):
            safe_eval("1 / 0")

    def test_empty_expression(self):
        with pytest.raises(ValueError, match="Empty expression"):
            safe_eval("")

    def test_too_long_expression(self):
        with pytest.raises(ValueError, match="too long"):
            safe_eval("1 + " * 100 + "1")

    def test_string_constant_rejected(self):
        with pytest.raises(ValueError, match="Unsupported constant"):
            safe_eval("'hello'")

    def test_assignment_rejected(self):
        with pytest.raises((ValueError, SyntaxError)):
            safe_eval("x = 5")


class TestSafeEvalFormat:
    """Result formatting."""

    def test_integer_result(self):
        result = safe_eval("2 + 2")
        assert isinstance(result, int)
        assert result == 4

    def test_float_result(self):
        result = safe_eval("10 / 4")
        assert isinstance(result, float)
        assert result == 2.5

    def test_large_integer(self):
        result = safe_eval("2 ** 64")
        assert isinstance(result, int)


# ── Tool definitions tests ───────────────────────────────────────────


class TestToolDefinitions:
    """Verify tool definitions format."""

    def test_definitions_count(self):
        assert len(TOOL_DEFINITIONS) == 6

    def test_all_have_function_type(self):
        for tool in TOOL_DEFINITIONS:
            assert tool["type"] == "function"

    def test_all_have_name(self):
        for tool in TOOL_DEFINITIONS:
            assert "name" in tool["function"]
            assert len(tool["function"]["name"]) > 0

    def test_all_have_description(self):
        for tool in TOOL_DEFINITIONS:
            assert "description" in tool["function"]
            assert len(tool["function"]["description"]) > 10

    def test_all_have_parameters(self):
        for tool in TOOL_DEFINITIONS:
            assert "parameters" in tool["function"]
            assert tool["function"]["parameters"]["type"] == "object"

    def test_known_tool_names(self):
        names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
        expected = {"get_current_time", "calculator", "web_search", "rag_search", "camera_snapshot", "time_calc"}
        assert names == expected

    def test_calculator_has_expression_param(self):
        calc = next(t for t in TOOL_DEFINITIONS if t["function"]["name"] == "calculator")
        props = calc["function"]["parameters"]["properties"]
        assert "expression" in props
        assert props["expression"]["type"] == "string"
        assert "expression" in calc["function"]["parameters"]["required"]

    def test_web_search_has_query_param(self):
        search = next(t for t in TOOL_DEFINITIONS if t["function"]["name"] == "web_search")
        props = search["function"]["parameters"]["properties"]
        assert "query" in props
        assert "query" in search["function"]["parameters"]["required"]

    def test_rag_search_has_query_param(self):
        rag = next(t for t in TOOL_DEFINITIONS if t["function"]["name"] == "rag_search")
        props = rag["function"]["parameters"]["properties"]
        assert "query" in props
        assert "query" in rag["function"]["parameters"]["required"]

    def test_camera_has_room_param(self):
        cam = next(t for t in TOOL_DEFINITIONS if t["function"]["name"] == "camera_snapshot")
        props = cam["function"]["parameters"]["properties"]
        assert "room" in props
        assert "room" in cam["function"]["parameters"]["required"]

    def test_get_tool_definitions_returns_data(self):
        defs = get_tool_definitions()
        assert len(defs) == 6
        # Verify it returns the same definitions
        assert defs is TOOL_DEFINITIONS


# ── execute_tool tests ───────────────────────────────────────────────


class TestExecuteTool:
    """Test tool execution dispatch."""

    def test_unknown_tool(self):
        result = execute_tool("nonexistent_tool", {}, {"lang": "ru"})
        assert "nonexistent" in result.lower() or "неизвестный" in result.lower()

    def test_get_current_time(self):
        result = execute_tool("get_current_time", {}, {"lang": "ru"})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_calculator_basic(self):
        result = execute_tool("calculator", {"expression": "2 + 2"}, {"lang": "ru"})
        assert result == "4"

    def test_calculator_complex(self):
        result = execute_tool("calculator", {"expression": "sqrt(144) + 3**2"}, {"lang": "ru"})
        assert result == "21"

    def test_calculator_division_by_zero(self):
        result = execute_tool("calculator", {"expression": "1/0"}, {"lang": "ru"})
        assert "деление на ноль" in result.lower() or "division by zero" in result.lower()

    def test_calculator_invalid_expression(self):
        result = execute_tool("calculator", {"expression": "invalid!!!"}, {"lang": "ru"})
        assert "ошибка" in result.lower() or "error" in result.lower()

    def test_time_calc_days_until_weekday(self):
        """Test days_until_weekday operation."""
        result = execute_tool("time_calc", {
            "operation": "days_until_weekday",
            "weekday": "понедельник",
            "date": "2026-06-11",
        }, {"lang": "ru"})
        assert result == "4"

    def test_time_calc_days_between(self):
        """Test days_between operation."""
        result = execute_tool("time_calc", {
            "operation": "days_between",
            "from_date": "2026-06-11",
            "to_date": "2026-06-15",
        }, {"lang": "ru"})
        assert result == "4"

    def test_time_calc_add_days(self):
        """Test add_days operation."""
        result = execute_tool("time_calc", {
            "operation": "add_days",
            "date": "2026-06-11",
            "days": 3,
        }, {"lang": "ru"})
        assert result is not None

    def test_time_calc_day_of_week(self):
        """Test day_of_week operation."""
        result = execute_tool("time_calc", {
            "operation": "day_of_week",
            "date": "2026-06-11",
        }, {"lang": "ru"})
        assert result.lower() in ("четверг", "thursday")

    def test_time_calc_days_until(self):
        """Test days_until_weekday for Monday from Friday."""
        result = execute_tool("time_calc", {
            "operation": "days_until_weekday",
            "weekday": "понедельник",
            "date": "2026-06-12",
        }, {"lang": "ru"})
        assert result == "3"

    def test_time_calc_format_date(self):
        """Test format_date operation."""
        result = execute_tool("time_calc", {
            "operation": "format_date",
            "date": "2026-06-11",
            "format": "iso",
        }, {"lang": "ru"})
        assert result == "2026-06-11"

    def test_time_calc_days_until_date(self):
        """Test days_until_date with fixed dates."""
        result = execute_tool("time_calc", {
            "operation": "days_between",
            "from_date": "2026-06-12",
            "to_date": "2026-06-30",
        }, {"lang": "ru"})
        assert result == "18"

    def test_time_calc_days_until_end_of_year(self):
        """Test days_until_end_of for year period."""
        result = execute_tool("time_calc", {
            "operation": "days_until_end_of",
            "period": "year",
        }, {"lang": "ru"})
        assert result is not None
        assert int(result) > 0

    def test_time_calc_days_until_end_of_quarter(self):
        """Test days_until_end_of for quarter period."""
        result = execute_tool("time_calc", {
            "operation": "days_until_end_of",
            "period": "quarter",
        }, {"lang": "ru"})
        assert result is not None
        assert int(result) > 0

    def test_time_calc_days_until_end_of_spring(self):
        """Test days_until_end_of for spring period."""
        result = execute_tool("time_calc", {
            "operation": "days_until_end_of",
            "period": "spring",
        }, {"lang": "ru"})
        assert result is not None

    def test_time_calc_days_until_end_of_summer(self):
        """Test days_until_end_of for summer period (meteorological: ends Aug 31)."""
        result = execute_tool("time_calc", {
            "operation": "days_until_end_of",
            "period": "summer",
        }, {"lang": "ru"})
        assert result is not None
        assert int(result) > 0

    def test_time_calc_unknown_period(self):
        """Test days_until_end_of with unknown period returns error."""
        result = execute_tool("time_calc", {
            "operation": "days_until_end_of",
            "period": "nonexistent",
        }, {"lang": "ru"})
        assert "error" in result.lower() or "ошибка" in result.lower()

    def test_time_calc_unknown_operation(self):
        """Test unknown operation returns error."""
        result = execute_tool("time_calc", {
            "operation": "nonexistent",
        }, {"lang": "ru"})
        assert "unknown" in result.lower() or "неизвестн" in result.lower()

    def test_time_calc_web_search_unavailable(self, app):
        """Web search without available module returns error."""
        with app.app_context():
            result = execute_tool("web_search", {"query": "test"}, {"app": app, "lang": "ru"})
            assert isinstance(result, str)

    def test_rag_search_unavailable(self, app):
        """RAG search without available module returns error."""
        with app.app_context():
            result = execute_tool("rag_search", {"query": "test"}, {"app": app, "lang": "ru"})
            assert isinstance(result, str)

    def test_camera_snapshot_unavailable(self, app):
        """Camera without available module returns error."""
        with app.app_context():
            result = execute_tool("camera_snapshot", {"room": "gos"}, {"app": app, "lang": "ru"})
            # Result is JSON string
            import json
            data = json.loads(result)
            assert data.get("success") is False

    def test_execution_error_handling(self):
        """Tool execution errors are caught and returned as strings."""
        # Pass invalid argument type to calculator
        result = execute_tool("calculator", {"expression": None}, {"lang": "ru"})
        assert isinstance(result, str)
