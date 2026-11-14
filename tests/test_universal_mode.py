"""Single-model ("universal") mode: the model filter and the role redirect.

The mode is a database row, so these tests exercise the two things that can
break without a GPU: which models are offered for the mode (a model without a
vision projector must never be listed) and whether the multimodal/reasoning
roles really resolve to the one universal row.
"""

from __future__ import annotations

import pytest

from app import model_config, universal_mode


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 16)
    return path


def test_find_mmproj_next_to_the_model(tmp_path, monkeypatch):
    monkeypatch.setattr(universal_mode, "MODELS_DIR", str(tmp_path))
    model = _touch(tmp_path / "Qwen3.6-35B" / "Qwen3.6-35B.gguf")
    projector = _touch(tmp_path / "Qwen3.6-35B" / "mmproj-F16.gguf")

    assert universal_mode.find_mmproj(str(model), "Qwen3.6-35B") == str(projector)


def test_find_mmproj_by_model_name_in_a_flat_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(universal_mode, "MODELS_DIR", str(tmp_path))
    model = _touch(tmp_path / "gemma-4-26B-A4B-it-UD-Q2_K_XL.gguf")
    _touch(tmp_path / "mmproj-gemma-4-12B-F16.gguf")
    projector = _touch(tmp_path / "mmproj-gemma-4-26B-A4B-it-UD-Q2_K_XL-F16.gguf")

    assert universal_mode.find_mmproj(str(model), "gemma-4-26B-A4B-it-UD-Q2_K_XL") == str(projector)


def test_find_mmproj_returns_none_for_a_text_model(tmp_path, monkeypatch):
    monkeypatch.setattr(universal_mode, "MODELS_DIR", str(tmp_path))
    model = _touch(tmp_path / "Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf")
    _touch(tmp_path / "mmproj-gemma-4-26B-A4B-it-UD-Q2_K_XL-F16.gguf")

    assert universal_mode.find_mmproj(str(model), "Qwen3.6-35B-A3B-UD-Q2_K_XL") is None


def test_available_models_lists_only_models_with_a_projector(tmp_path, monkeypatch):
    monkeypatch.setattr(universal_mode, "MODELS_DIR", str(tmp_path))
    monkeypatch.setattr("app.utils.get_gguf_models_cached", lambda *a, **k: {})

    _touch(tmp_path / "Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf")
    _touch(tmp_path / "mmproj-Qwen3.6-35B-A3B-UD-Q2_K_XL-F16.gguf")
    _touch(tmp_path / "bge-m3-Q8_0.gguf")

    names = [item["name"] for item in universal_mode.available_models()]

    assert "Qwen3.6-35B-A3B-UD-Q2_K_XL" in names
    assert "bge-m3-Q8_0" not in names


def test_role_resolves_to_the_universal_row(monkeypatch):
    rows = {
        "universal": {"module": "universal", "model_name": "Qwen3.6-35B", "context_length": 24576},
        "multimodal": {"module": "multimodal", "model_name": "Qwen3VL-8B", "context_length": 32768},
        "reasoning": {"module": "reasoning", "model_name": "Qwen3.6-35B", "context_length": 24576},
    }
    monkeypatch.setattr(model_config, "_read_row", lambda module: rows.get(module))

    multimodal = model_config.get_model_config("multimodal")
    reasoning = model_config.get_model_config("reasoning")

    assert multimodal["model_name"] == "Qwen3.6-35B"
    assert multimodal["universal"] is True
    assert multimodal["module"] == "multimodal"
    assert reasoning["model_name"] == "Qwen3.6-35B"


def test_empty_universal_row_keeps_the_pair(monkeypatch):
    rows = {
        "universal": {"module": "universal", "model_name": None},
        "multimodal": {"module": "multimodal", "model_name": "Qwen3VL-8B", "context_length": 32768},
    }
    monkeypatch.setattr(model_config, "_read_row", lambda module: rows.get(module))

    assert model_config.get_model_config("multimodal")["model_name"] == "Qwen3VL-8B"
    assert model_config.get_model_config("multimodal").get("universal") is None


@pytest.mark.parametrize(
    ("model_type", "expected"),
    [("reasoning", False), ("multimodal", True), ("embedding", True)],
)
def test_thinking_is_disabled_only_outside_the_reasoning_role(monkeypatch, model_type, expected):
    from app import llamacpp_client

    monkeypatch.setattr(universal_mode, "is_enabled", lambda: True)

    assert llamacpp_client._thinking_kwargs_needed(model_type) is expected


def test_thinking_stays_off_in_two_model_mode(monkeypatch):
    from app import llamacpp_client

    monkeypatch.setattr(universal_mode, "is_enabled", lambda: False)

    assert llamacpp_client._thinking_kwargs_needed("multimodal") is False


def test_a_vision_model_without_a_thinking_phase_is_not_universal(tmp_path, monkeypatch):
    """Qwen3-VL-8B has a projector but is a chat model, not a reasoning one."""
    monkeypatch.setattr(universal_mode, "MODELS_DIR", str(tmp_path))
    monkeypatch.setattr("app.utils.get_gguf_models_cached", lambda *a, **k: {})

    _touch(tmp_path / "Qwen3VL-8B-Instruct-Q4_K_M.gguf")
    _touch(tmp_path / "mmproj-Qwen3VL-8B-Instruct-Q4_K_M-F16.gguf")

    assert universal_mode.available_models() == []
    assert universal_mode.supports_reasoning("Qwen3VL-8B-Instruct-Q4_K_M") is False


def test_a_reasoning_model_with_a_projector_is_universal(tmp_path, monkeypatch):
    monkeypatch.setattr(universal_mode, "MODELS_DIR", str(tmp_path))
    monkeypatch.setattr("app.utils.get_gguf_models_cached", lambda *a, **k: {})

    _touch(tmp_path / "Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf")
    _touch(tmp_path / "mmproj-Qwen3.6-35B-A3B-UD-Q2_K_XL-F16.gguf")

    names = [item["name"] for item in universal_mode.available_models()]
    assert names == ["Qwen3.6-35B-A3B-UD-Q2_K_XL"]


def _fake_gguf(path, template: str) -> None:
    """A minimal GGUF header: tensor count 0, one string array and one template."""
    import struct

    def s(value: str) -> bytes:
        raw = value.encode("utf-8")
        return struct.pack("<Q", len(raw)) + raw

    entries = []
    entries.append(s("general.architecture") + struct.pack("<I", 8) + s("qwen35moe"))
    tokens = ["<s>", "</s>"]
    entries.append(
        s("tokenizer.ggml.tokens")
        + struct.pack("<I", 9)
        + struct.pack("<I", 8)
        + struct.pack("<Q", len(tokens))
        + b"".join(s(token) for token in tokens)
    )
    entries.append(s("tokenizer.chat_template") + struct.pack("<I", 8) + s(template))
    path.write_bytes(b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", 3) + b"".join(entries))


def test_the_chat_template_is_read_from_the_header_only(tmp_path, monkeypatch):
    """The fast reader must skip arrays and return the template unchanged."""
    monkeypatch.setattr(universal_mode, "MODELS_DIR", str(tmp_path))
    model = tmp_path / "Qwen3.6-35B.gguf"
    template = "{% if enable_thinking %}think first{% endif %}{{ messages }}"
    _fake_gguf(model, template)

    assert universal_mode._chat_template(str(model)) == template
    assert universal_mode.supports_reasoning("Qwen3.6-35B", str(model)) is True


def test_a_template_without_a_thinking_phase_is_not_reasoning(tmp_path, monkeypatch):
    monkeypatch.setattr(universal_mode, "MODELS_DIR", str(tmp_path))
    model = tmp_path / "Qwen3VL-8B.gguf"
    _fake_gguf(model, "{{ messages }}")

    assert universal_mode.supports_reasoning("Qwen3VL-8B", str(model)) is False
