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


def test_scenario_and_document_counts_per_language():
    for lang in ("ru", "en"):
        data = _scenarios(lang)
        # Ten conversations plus seven background tasks.
        assert len(data["scenarios"]) == 17
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


def test_demo_snapshots_have_no_system_errors():
    for lang in ("ru", "en"):
        raw = (DEMO / f"snapshot/{lang}.json").read_text("utf-8")
        for marker in ("⚠️", "Память GPU", "GPU memory", "План обновлён", "Plan updated"):
            assert marker not in raw, f"{lang}: {marker}"


def test_video_messages_name_a_real_backend():
    backends = ("wan2.2-ti2v-5b-turbo", "ltxv-2b-0.9.8-distilled")
    for lang in ("ru", "en"):
        snapshot = json.loads((DEMO / f"snapshot/{lang}.json").read_text("utf-8"))

        def walk(value, lang=lang):
            if isinstance(value, dict):
                if str(value.get("file_type", "")).startswith("video"):
                    model = str(value.get("model_name") or "")
                    assert "injected" not in model, f"{lang}: {value.get('file_path')} has no backend label"
                    assert any(name in model for name in backends), f"{lang}: {model}"
                    assert value.get("gen_model") == value.get("model_name")
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(snapshot)
