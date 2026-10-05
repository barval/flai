"""Crawler settings parse with the spec defaults and the allow-list flag."""

from flask import Flask

from app import config as config_mod


def _load(monkeypatch, **overrides):
    monkeypatch.setenv("SECRET_KEY", "crawler-config-test")
    for key, value in overrides.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    flask_app = Flask(__name__)
    config_mod.load_config(flask_app)
    return flask_app.config


def _unset_all(monkeypatch):
    for key in (
        "CRAWL_ENABLED",
        "CRAWLER_URL",
        "CRAWL_MAX_PAGES",
        "CRAWL_MAX_DEPTH",
        "CRAWL_TIMEOUT_S",
        "CRAWL_PAGE_TIMEOUT_S",
        "CRAWL_MAX_PAGE_CHARS",
        "CRAWL_MAX_TOTAL_CHARS",
        "CRAWL_CONCURRENCY",
    ):
        monkeypatch.delenv(key, raising=False)


def test_defaults(monkeypatch):
    _unset_all(monkeypatch)
    cfg = _load(monkeypatch)
    assert cfg["CRAWL_ENABLED"] is False
    assert cfg["CRAWLER_URL"] == "http://flai-crawler:11235"
    assert cfg["CRAWL_MAX_PAGES"] == 50
    assert cfg["CRAWL_MAX_DEPTH"] == 3
    assert cfg["CRAWL_TIMEOUT_S"] == 300
    assert cfg["CRAWL_PAGE_TIMEOUT_S"] == 30
    assert cfg["CRAWL_MAX_PAGE_CHARS"] == 50000
    assert cfg["CRAWL_MAX_TOTAL_CHARS"] == 1000000
    assert cfg["CRAWL_CONCURRENCY"] == 2


def test_enabled_flag_uses_allow_list(monkeypatch):
    _unset_all(monkeypatch)
    assert _load(monkeypatch, CRAWL_ENABLED="true")["CRAWL_ENABLED"] is True
    assert _load(monkeypatch, CRAWL_ENABLED="1")["CRAWL_ENABLED"] is True
    assert _load(monkeypatch, CRAWL_ENABLED="yes")["CRAWL_ENABLED"] is True
    for off in ("false", "0", "no", "off", "disabled", ""):
        assert _load(monkeypatch, CRAWL_ENABLED=off)["CRAWL_ENABLED"] is False


def test_numeric_overrides(monkeypatch):
    _unset_all(monkeypatch)
    cfg = _load(monkeypatch, CRAWL_MAX_PAGES="7", CRAWL_TIMEOUT_S="60")
    assert cfg["CRAWL_MAX_PAGES"] == 7
    assert cfg["CRAWL_TIMEOUT_S"] == 60
