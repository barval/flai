# tests/test_graph_settings.py
"""Graph settings: the admin group, the defaults and the env files."""

import re
from pathlib import Path

import pytest

GRAPH_KEYS = [
    "GRAPH_RAG_ENABLED",
    "GRAPH_AUTO_EXTRACT",
    "GRAPH_EXTRACT_MODEL",
    "GRAPH_HOPS",
    "GRAPH_MAX_SEEDS",
    "GRAPH_MAX_EDGES",
    "GRAPH_SHARE",
    "GRAPH_SECTION_TOKENS",
    "GRAPH_MAX_NODES",
    "GRAPH_DOC_SIMILARITY",
    "GRAPH_DOC_NEIGHBOURS",
]


def _graph_keys(path: str) -> set[str]:
    text = Path(path).read_text(encoding="utf-8")
    return set(re.findall(r"^(?:#\s*)?(GRAPH_[A-Z_]+)=", text, re.MULTILINE))


@pytest.mark.unit
def test_the_admin_group_lists_every_key(test_app):
    from app.settings_registry import build_schema

    groups = {group["id"]: group for group in build_schema(test_app)}

    assert "graph" in groups
    assert {field["key"] for field in groups["graph"]["fields"]} == set(GRAPH_KEYS)


@pytest.mark.unit
def test_the_defaults_match_the_design(test_app):
    assert test_app.config["GRAPH_RAG_ENABLED"] is False
    assert test_app.config["GRAPH_AUTO_EXTRACT"] == "auto"
    assert test_app.config["GRAPH_HOPS"] == 2
    assert test_app.config["GRAPH_MAX_NODES"] == 400
    assert test_app.config["GRAPH_SHARE"] == pytest.approx(0.15)


@pytest.mark.unit
def test_the_example_env_carries_every_key():
    assert _graph_keys(".env.example") == set(GRAPH_KEYS)


@pytest.mark.unit
def test_a_local_env_matches_the_example():
    # .env is untracked and absent in CI; when it exists it must agree.
    if not Path(".env").exists():
        pytest.skip(".env is not present")
    assert _graph_keys(".env") == _graph_keys(".env.example")


@pytest.mark.unit
def test_auto_extraction_follows_the_platform(test_app):
    from app.config import graph_auto_extract_enabled

    test_app.config["GRAPH_AUTO_EXTRACT"] = "auto"
    test_app.config["FLAI_PLATFORM"] = "cpu"
    assert graph_auto_extract_enabled(test_app) is False

    test_app.config["FLAI_PLATFORM"] = "nvidia"
    assert graph_auto_extract_enabled(test_app) is True

    test_app.config["GRAPH_AUTO_EXTRACT"] = "off"
    assert graph_auto_extract_enabled(test_app) is False
