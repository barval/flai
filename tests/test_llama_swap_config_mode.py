"""llama-swap generation in one-model and two-model modes.

The mode decides how many LLM entries exist at all: two-model mode emits the
multimodal and reasoning pair, single-model mode replaces them with one entry.
These tests pin that shape without a database or a GPU.
"""

from __future__ import annotations

import app.universal_mode as universal_mode
from app.llama_swap_config import LlamaSwapConfigGenerator


def _generator(monkeypatch):
    gen = LlamaSwapConfigGenerator()

    def entry(module, ngl_override=None):
        return {module: {"name": module, "cmd": f"llama-server -m /models/{module}.gguf", "aliases": [f"{module}.gguf"]}}

    monkeypatch.setattr(gen, "build_model_entry", entry)
    monkeypatch.setattr(gen, "build_merge_cpu_entry", lambda: None)
    monkeypatch.setattr(gen, "build_reranker_entry", lambda: None)
    monkeypatch.setattr(gen, "_cpu_swap_enabled", lambda: False)
    return gen


def test_single_model_mode_emits_one_llm_entry(monkeypatch):
    monkeypatch.setattr(universal_mode, "is_enabled", lambda: True)
    yaml = _generator(monkeypatch).generate_yaml()

    assert "  universal:" in yaml
    assert "  multimodal:" not in yaml
    assert "  reasoning:" not in yaml
    assert "members: [embedding, universal]" in yaml


def test_two_model_mode_keeps_the_pair(monkeypatch):
    monkeypatch.setattr(universal_mode, "is_enabled", lambda: False)
    yaml = _generator(monkeypatch).generate_yaml()

    assert "  multimodal:" in yaml
    assert "  reasoning:" in yaml
    assert "  universal:" not in yaml
    assert "members: [embedding, reasoning, multimodal]" in yaml


def test_universal_entry_is_never_evicted():
    """One model serves every role: evicting it costs an 11-14 GB reload."""
    from app.llama_swap_config import DEFAULT_TTL, GROUP_SETTINGS

    assert DEFAULT_TTL["universal"] == 0
    assert GROUP_SETTINGS["universal"]["group"] == "llm_fast"
