# tests/test_admin_token_format.py
"""Token totals in the Users tab must be numbers by the time they reach the browser."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_admin_js_coerces_token_totals():
    """A string makes toLocaleString() a no-op and prints the digits ungrouped."""
    source = (ROOT / "app/static/js/admin.js").read_text(encoding="utf-8")
    assert "Number(user.outgoing_tokens" in source
    assert "Number(user.incoming_tokens" in source


def test_admin_route_casts_sql_sums_to_int():
    """SQL numeric arrives as Decimal, which Flask serializes as a string."""
    source = (ROOT / "app/routes/admin.py").read_text(encoding="utf-8")
    assert 'u_dict["outgoing_tokens"] = int(' in source
    assert 'u_dict["incoming_tokens"] = int(' in source
