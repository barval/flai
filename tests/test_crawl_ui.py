# tests/test_crawl_ui.py
"""Stage wiring and compose profile for the crawler."""

import re


def test_stage_keys_registered():
    with open("app/static/js/events.js", encoding="utf-8") as f:
        src = f.read()
    assert "crawl_start: 'stage_crawl_start'" in src
    assert "crawl_page: 'stage_crawl_page'" in src
    assert "crawl_indexing: 'stage_crawl_indexing'" in src
    assert re.search(r"STAGE_COUNTER_KEYS = \{[^}]*crawl_page", src, re.S)


def test_stage_msgids_in_both_catalogs():
    for path in (
        "translations/ru/LC_MESSAGES/messages.po",
        "translations/en/LC_MESSAGES/messages.po",
    ):
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for msgid in ("stage_crawl_start", "stage_crawl_page", "stage_crawl_indexing"):
            assert f'msgid "{msgid}"' in src, f"{msgid} missing in {path}"


def test_compose_defines_crawler_service():
    with open("docker-compose.gpu.yml", encoding="utf-8") as f:
        src = f.read()
    assert "crawler:" in src
    assert "with-crawler" in src
    crawler_block = None
    if "  crawler:" in src:
        idx = src.find("  crawler:")
        rest = src[idx + 1 :]
        m = re.search(r"^( {2}[a-zA-Z0-9_-]+:)", rest, re.M)
        if m:
            end = idx + 1 + m.start()
            crawler_block = src[idx:end]
        else:
            crawler_block = src[idx:]
    assert crawler_block is not None
    assert "ports:" not in crawler_block
