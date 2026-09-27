"""Unit tests for the HF catalog client in app/model_hub.py (no network)."""

import struct

import pytest

from app import model_hub


class _Resp:
    def __init__(self, payload, headers=None, status_code=200):
        self._payload = payload
        self.headers = headers or {}
        self.status_code = status_code

    def json(self):
        return self._payload


def _router_fake_hf_get(full_repos):
    """Returns dict url->payload."""
    state = {"search_calls": 0}

    def fake(url, params=None, stream=False, extra_headers=None, timeout=30):
        if "search=" in url or "search" in (params or {}):
            state["search_calls"] += 1
            return _Resp(
                [
                    {
                        "id": "org/Awesome-Model",
                        "downloads": 99,
                        "likes": 5,
                        "gated": True,
                        "private": False,
                        "siblings": [
                            {"rfilename": "model.Q4_K_M.gguf", "size": 2516582400, "lfs": {"oid": "a" * 64}},
                            {"rfilename": "mmproj-f16.gguf", "size": 104857600},
                            {"rfilename": "README.md", "size": 100},
                        ],
                    },
                    {
                        "id": "secret/repo",
                        "downloads": 1,
                        "likes": 0,
                        "gated": False,
                        "private": True,
                        "siblings": [{"rfilename": "x.gguf", "size": 10}],
                    },
                ]
            )
        repo_id = url.rsplit("/", 1)[-1]
        return _Resp(full_repos[repo_id])

    return fake, state


def test_search_hf_filters_gguf_and_private(monkeypatch):
    full = {
        "Awesome-Model": {
            "id": "org/Awesome-Model",
            "siblings": [{"rfilename": "model.Q4_K_M.gguf", "size": 2516582400, "lfs": {"oid": "a" * 64}}],
        },
        "repo": {"id": "secret/repo", "siblings": [{"rfilename": "x.gguf", "size": 10}]},
    }
    model_hub._files_cache.clear()
    fake, state = _router_fake_hf_get(full)
    monkeypatch.setattr(model_hub, "_hf_get", fake)
    items = model_hub.search_hf("q", 5)
    assert state["search_calls"] == 1
    assert len(items) == 1
    assert items[0]["repo"] == "org/Awesome-Model"
    assert items[0]["gated"] is True
    assert items[0]["files"] == [
        {"path": "model.Q4_K_M.gguf", "size_mb": 2400.0, "sha256": "a" * 64},
    ]


def test_get_repo_arch_dense_and_moe(monkeypatch):
    calls = {"n": 0}

    def fake(url, params=None, stream=False, extra_headers=None, timeout=30):
        calls["n"] += 1
        if url.endswith("/tree/main"):
            return _Resp([{"path": "config.json", "type": "file"}])
        return _Resp(
            {
                "architectures": ["Qwen3ForCausalLM"],
                "num_hidden_layers": 36,
                "num_local_experts": 8,
            }
        )

    monkeypatch.setattr(model_hub, "_hf_get", fake)
    arch = model_hub.get_repo_arch("org/My")
    assert arch["block_count"] == 36
    assert arch["expert_count"] == 8
    assert arch["arch"] == ["Qwen3ForCausalLM"]
    assert calls["n"] == 2  # tree + resolve


def test_get_repo_arch_none_when_missing(monkeypatch):
    def fake(url, params=None, stream=False, extra_headers=None, timeout=30):
        return _Resp({"detail": "nope"}, status_code=404)

    monkeypatch.setattr(model_hub, "_hf_get", fake)
    assert model_hub.get_repo_arch("nope/x") is None


def test_estimate_fit_uses_classifier(monkeypatch):
    monkeypatch.setattr(
        model_hub, "_repo_files", lambda repo, limit=50: [{"path": "model.gguf", "size_mb": 2400, "sha256": "a" * 64}]
    )
    monkeypatch.setattr(
        model_hub, "get_repo_arch", lambda repo: {"block_count": 36, "expert_count": 8, "arch": ["Qwen3ForCausalLM"]}
    )
    monkeypatch.setattr(
        model_hub,
        "_classify_model_fit",
        lambda *a, **k: {
            "tier": "good",
            "can_save": True,
            "ngl_recommended": 36,
            "vram_mb": 1000,
            "file_mb": 2400,
            "kv_cache_mb": 100,
            "message": "✓ Fits in VRAM",
        },
    )
    fit = model_hub.estimate_fit("org/My", "model.gguf", module="multimodal", context_length=8192)
    assert fit["tier"] == "good"
    assert fit["expert_count"] == 8
    assert fit["block_count"] == 36
    assert fit["sha256"] == "a" * 64


def _gguf_string(value):
    encoded = value.encode("utf-8")
    return struct.pack("<Q", len(encoded)) + encoded


def _gguf_metadata_header():
    fields = [
        (
            "general.tags",
            9,
            struct.pack("<IQ", 8, 2) + _gguf_string("gguf") + _gguf_string("chat"),
        ),
        ("general.architecture", 8, _gguf_string("qwen35")),
        ("qwen35.block_count", 4, struct.pack("<I", 32)),
        ("qwen35.context_length", 4, struct.pack("<I", 262144)),
        ("qwen35.expert_count", 4, struct.pack("<I", 8)),
    ]
    header = b"GGUF" + struct.pack("<IQQ", 3, 0, len(fields))
    return header + b"".join(_gguf_string(key) + struct.pack("<I", kind) + value for key, kind, value in fields)


def test_estimate_fit_uses_gguf_metadata_when_config_is_missing(monkeypatch):
    monkeypatch.setattr(model_hub, "_gguf_arch_cache", {}, raising=False)
    files = [
        {"path": "model-Q4_K_M.gguf", "size_mb": 2400, "sha256": "a" * 64},
        {"path": "model-Q5_K_M.gguf", "size_mb": 2800, "sha256": "b" * 64},
    ]
    range_requests = []

    class _RangeResp:
        status_code = 206
        url = "https://huggingface.co/org/My/resolve/main/model-Q4_K_M.gguf"

        def iter_content(self, chunk_size):
            yield _gguf_metadata_header()

    def fake_hf_get(url, params=None, stream=False, extra_headers=None, timeout=30):
        range_requests.append((url, extra_headers))
        return _RangeResp()

    monkeypatch.setattr(model_hub, "_repo_files", lambda repo, limit=50: files)
    monkeypatch.setattr(model_hub, "get_repo_arch", lambda repo: None)
    monkeypatch.setattr(model_hub, "_hf_get", fake_hf_get)
    monkeypatch.setattr(
        model_hub,
        "_classify_model_fit",
        lambda *args, **kwargs: {"tier": "good", "arch_max_ctx": 0},
    )

    first = model_hub.estimate_fit("org/My", files[0]["path"], module="reasoning", context_length=8192)
    second = model_hub.estimate_fit("org/My", files[1]["path"], module="reasoning", context_length=8192)

    assert first["block_count"] == second["block_count"] == 32
    assert first["expert_count"] == second["expert_count"] == 8
    assert first["arch"] == second["arch"] == "qwen35"
    assert first["arch_max_ctx"] == second["arch_max_ctx"] == 262144
    assert len(range_requests) == 1
    assert range_requests[0][1]["Range"].startswith("bytes=0-")
    assert int(range_requests[0][1]["Range"].split("-")[1]) < 10 * 1024 * 1024


def test_estimate_fit_blocked_without_arch(monkeypatch):
    monkeypatch.setattr(
        model_hub, "_repo_files", lambda repo, limit=50: [{"path": "model.gguf", "size_mb": 2400, "sha256": ""}]
    )
    monkeypatch.setattr(model_hub, "get_repo_arch", lambda repo: None)
    monkeypatch.setattr(model_hub, "_gguf_arch", lambda repo, path: None)
    with pytest.raises(model_hub.DownloadBlocked) as exc:
        model_hub.estimate_fit("org/My", "model.gguf")
    assert exc.value.reason == "unknown_arch"


def test_license_hint():
    assert model_hub.license_hint("apache-2.0") == "ok"
    assert model_hub.license_hint("cc-by-nc-4.0") == "nc"
    assert model_hub.license_hint("non-commercial") == "nc"
    assert model_hub.license_hint(None) == "ok"


def test_classify_type_multimodal_by_arch():
    assert model_hub.classify_model_type("Qwen/Qwen2-VL", ["Qwen2VLForConditionalGeneration"]) == "multimodal"
    assert model_hub.classify_model_type("LmStudio/Mllama", ["MllamaForConditionalGeneration"]) == "multimodal"


def test_classify_type_embedding_by_arch():
    assert model_hub.classify_model_type("BAAI/bge-large", ["BgeModel"]) == "embedding"
    assert model_hub.classify_model_type("nomic-ai/nomic", ["NomicBertModel"]) == "embedding"


def test_classify_type_reasoning_default():
    assert model_hub.classify_model_type("Qwen/Qwen3", ["Qwen3ForCausalLM"]) == "reasoning"


def test_classify_type_filename_fallback_when_no_arch():
    assert model_hub.classify_model_type("BAAI/bge-m3", []) == "embedding"
    assert model_hub.classify_model_type("foo-b/model-vl", []) == "multimodal"
    assert model_hub.classify_model_type("foo/plain-model", []) == "reasoning"


def test_get_repo_arch_includes_max_ctx(monkeypatch):
    model_hub._arch_cache.clear()

    def fake(url, params=None, stream=False, extra_headers=None, timeout=30):
        if url.endswith("/tree/main"):
            return _Resp([{"path": "config.json", "type": "file"}])
        return _Resp(
            {
                "architectures": ["Qwen2VLForConditionalGeneration"],
                "num_hidden_layers": 36,
                "num_local_experts": 0,
                "max_position_embeddings": 32768,
                "rope_scaling": {"type": "yarn", "factor": 2.0},
            }
        )

    monkeypatch.setattr(model_hub, "_hf_get", fake)
    arch = model_hub.get_repo_arch("org/My")
    assert arch["arch_max_ctx"] == 65536


def test_get_repo_arch_max_ctx_zero_when_missing(monkeypatch):
    model_hub._arch_cache.clear()

    def fake(url, params=None, stream=False, extra_headers=None, timeout=30):
        if url.endswith("/tree/main"):
            return _Resp([{"path": "config.json", "type": "file"}])
        return _Resp({"architectures": ["Qwen3ForCausalLM"], "num_hidden_layers": 36})

    monkeypatch.setattr(model_hub, "_hf_get", fake)
    arch = model_hub.get_repo_arch("org/My")
    assert arch["arch_max_ctx"] == 0


def test_search_hf_items_include_type_and_max_ctx(monkeypatch):
    full = {
        "Awesome-Model": {
            "id": "org/Awesome-Model",
            "siblings": [{"rfilename": "model.Q4_K_M.gguf", "size": 2516582400, "lfs": {"oid": "a" * 64}}],
        },
    }
    model_hub._files_cache.clear()
    model_hub._arch_cache.clear()
    fake, _state = _router_fake_hf_get(full)
    monkeypatch.setattr(model_hub, "_hf_get", fake)
    monkeypatch.setattr(
        model_hub, "get_repo_arch", lambda repo: {"block_count": 36, "arch_max_ctx": 4096, "arch": ["BgeModel"]}
    )
    items = model_hub.search_hf("q", 5)
    assert items[0]["type"] == "embedding"
    assert items[0]["arch_max_ctx"] == 4096
