"""Tests for the bilingual demo scenarios and their documents."""

import json
import re
from pathlib import Path

DEMO = Path("demo")
CYRILLIC = re.compile(r"[\u0400-\u04FF]")
KINDS = {"text", "image", "video", "rlm", "task"}


def _scenarios(lang):
    return json.loads((DEMO / f"scenarios/{lang}.json").read_text("utf-8"))


def test_english_scenarios_are_cyrillic_free():
    assert not CYRILLIC.search(json.dumps(_scenarios("en"), ensure_ascii=False))


def test_russian_scenarios_have_cyrillic():
    assert CYRILLIC.search(json.dumps(_scenarios("ru"), ensure_ascii=False))


def test_ten_scenarios_and_three_documents_per_language():
    for lang in ("ru", "en"):
        data = _scenarios(lang)
        assert len(data["scenarios"]) == 16
        assert len(data["documents"]) == 3


def test_every_scenario_is_well_formed():
    for lang in ("ru", "en"):
        for scenario in _scenarios(lang)["scenarios"]:
            assert scenario["kind"] in KINDS
            assert scenario["prompt"].strip()
            assert scenario["title"].strip()


def test_documents_exist_and_are_pdf():
    for lang in ("ru", "en"):
        for document in _scenarios(lang)["documents"]:
            path = DEMO / document["source"]
            assert path.read_bytes().startswith(b"%PDF")
            assert path.stat().st_size > 20_000
