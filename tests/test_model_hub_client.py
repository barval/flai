"""Unit tests for the HF catalog client in app/model_hub.py (no network)."""

import os
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
        {"path": "model.Q4_K_M.gguf", "size_mb": 2400.0, "sha256": "a" * 64, "companion_mb": 0.0},
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


def test_estimate_fit_aux_file_does_not_poison_repo_arch(monkeypatch):
    """An imatrix / MTP / split-shard aux file (no block_count) must not poison
    the repo-wide GGUF arch cache used by real model files."""
    monkeypatch.setattr(model_hub, "_gguf_arch_cache", {}, raising=False)
    no_block = b"GGUF" + struct.pack("<IQQ", 3, 0, 0)  # kv_count = 0 -> no block_count
    good = _gguf_metadata_header()
    payloads = {"aux.gguf": no_block, "Q4_K_M.gguf": good}
    requested = []

    class _Resp:
        status_code = 206
        url = "https://huggingface.co/org/P/resolve/main/aux.gguf"

        def __init__(self, payload):
            self._payload = payload

        def iter_content(self, chunk_size):
            yield self._payload

    def fake_hf_get(url, params=None, stream=False, extra_headers=None, timeout=30):
        requested.append(url)
        return _Resp(payloads[url.rstrip("/").rsplit("/", 1)[-1]])

    monkeypatch.setattr(
        model_hub,
        "_repo_files",
        lambda repo, limit=50: [
            {"path": "aux.gguf", "size_mb": 13, "sha256": "a" * 64},
            {"path": "Q4_K_M.gguf", "size_mb": 2400, "sha256": "b" * 64},
        ],
    )
    monkeypatch.setattr(model_hub, "get_repo_arch", lambda repo: None)
    monkeypatch.setattr(model_hub, "_hf_get", fake_hf_get)
    monkeypatch.setattr(
        model_hub,
        "_classify_model_fit",
        lambda *args, **kwargs: {"tier": "good", "arch_max_ctx": 0},
    )

    with pytest.raises(model_hub.DownloadBlocked):
        model_hub.estimate_fit("org/P", "aux.gguf", module="reasoning")
    fit = model_hub.estimate_fit("org/P", "Q4_K_M.gguf", module="reasoning")

    assert fit["block_count"] == 32
    assert any(url.endswith("Q4_K_M.gguf") for url in requested)


def test_aux_file_detection():
    """imatrix files, MTP heads and non-first shards are auxiliary and must be
    hidden from the model list; -mtp models and first shards are real models."""
    aux = [
        "imatrix_unsloth.gguf",
        "imatrix-qwen3.8-27b.gguf",
        "MTP/mtp-Qwen3.8-27B-Q4_0.gguf",
        "mtp-X.gguf",
        "BF16/Qwen3.8-27B-BF16-00002-of-00002.gguf",
        "tokenizer.gguf",
        "generated/llm2vec-text-bundle/tokenizer.gguf",
        "generated/llm2vec-text-bundle/final-norm.gguf",
    ]
    models = [
        "Qwen3.8-27B-UD-IQ1_S.gguf",
        "Qwen3.8-27B-GSQ-RCO-IQ2_XS-mtp.gguf",
        "BF16/Qwen3.8-27B-BF16-00001-of-00002.gguf",
        "subdir/tokenizer-model-Q4_K_M.gguf",
    ]
    for path in aux:
        assert model_hub.is_aux_file(path), path
    for path in models:
        assert not model_hub.is_aux_file(path), path


def test_companion_files_shard_tail_and_mtp(monkeypatch):
    files = [
        {"path": "BF16/My-BF16-00001-of-00002.gguf", "size_mb": 2.0, "sha256": "a" * 64},
        {"path": "BF16/My-BF16-00002-of-00002.gguf", "size_mb": 2.0, "sha256": "b" * 64},
        {"path": "My-Q4_K_M.gguf", "size_mb": 2.0, "sha256": "c" * 64},
        {"path": "My.Unsuitable-IQ1_S.gguf", "size_mb": 2.0, "sha256": "j" * 64},
        {"path": "MTP/mtp-My-BF16.gguf", "size_mb": 1.0, "sha256": "d" * 64},
        {"path": "MTP/mtp-My-Q4_K_M.gguf", "size_mb": 1.0, "sha256": "e" * 64},
    ]
    monkeypatch.setattr(model_hub, "_repo_files", lambda repo, limit=50: files)

    comps = model_hub._companion_files("org/My", "BF16/My-BF16-00001-of-00002.gguf")
    assert [c["path"] for c in comps] == ["BF16/My-BF16-00002-of-00002.gguf", "MTP/mtp-My-BF16.gguf"]

    comps = model_hub._companion_files("org/My", "My-Q4_K_M.gguf")
    assert [c["path"] for c in comps] == ["MTP/mtp-My-Q4_K_M.gguf"]

    comps = model_hub._companion_files("org/My", "My.Unsuitable-IQ1_S.gguf")
    assert [c["path"] for c in comps] == [], "a missing quant-matched MTP head must not pull every family head"


def test_runtime_size_mb_sums_shards_only(monkeypatch):
    files = [
        {"path": "BF16/My-00001-of-00003.gguf", "size_mb": 8.0, "sha256": "a" * 64},
        {"path": "BF16/My-00002-of-00003.gguf", "size_mb": 8.0, "sha256": "b" * 64},
        {"path": "BF16/My-00003-of-00003.gguf", "size_mb": 8.0, "sha256": "c" * 64},
        {"path": "My.Q8_0.gguf", "size_mb": 9.0, "sha256": "d" * 64},
        {"path": "MTP/mtp-My-Q8_0.gguf", "size_mb": 4.0, "sha256": "e" * 64},
    ]
    monkeypatch.setattr(model_hub, "_repo_files", lambda repo, limit=50: files)

    assert model_hub._runtime_size_mb("org/My", "BF16/My-00001-of-00003.gguf") == 24.0
    assert model_hub._runtime_size_mb("org/My", "My.Q8_0.gguf") == 9.0
    assert model_hub._runtime_size_mb("org/My", "no-such.gguf") == 0.0


def test_search_hf_keeps_only_model_files(monkeypatch):
    """search_hf returns only model files, with the sum of their service-file
    sizes in companion_mb."""
    files = [
        {"path": "Qwen3.8-27B-UD-IQ1_S.gguf", "size_mb": 5632.0, "sha256": "a" * 64},
        {"path": "imatrix_unsloth.gguf", "size_mb": 13.0, "sha256": "b" * 64},
        {"path": "MTP/mtp-Qwen3.8-27B-Q4_0.gguf", "size_mb": 1306.0, "sha256": "c" * 64},
        {"path": "MTP/mtp-Qwen3.8-27B-BF16.gguf", "size_mb": 3000.0, "sha256": "f" * 64},
        {"path": "Qwen3.8-27B-BF16-00001-of-00002.gguf", "size_mb": 22732.0, "sha256": "d" * 64},
        {"path": "Qwen3.8-27B-BF16-00002-of-00002.gguf", "size_mb": 22732.0, "sha256": "e" * 64},
    ]
    monkeypatch.setattr(model_hub, "_repo_files", lambda repo, limit=50: files)
    search_payload = [{"id": "org/Repo", "downloads": 1, "likes": 0, "gated": False, "license": "apache-2.0"}]
    monkeypatch.setattr(model_hub, "_hf_get", lambda *a, **k: _Resp(search_payload))
    monkeypatch.setattr(model_hub, "get_repo_arch", lambda repo: None)
    monkeypatch.setattr(model_hub, "_gguf_arch", lambda repo, path: {"block_count": 64})

    items = model_hub.search_hf("q", 5)
    assert len(items) == 1
    paths = [f["path"] for f in items[0]["files"]]
    assert paths == ["Qwen3.8-27B-UD-IQ1_S.gguf", "Qwen3.8-27B-BF16-00001-of-00002.gguf"]
    assert all(bad not in paths for bad in ("imatrix_unsloth.gguf", "MTP/mtp-Qwen3.8-27B-Q4_0.gguf"))
    assert all("00002-of-00002" not in p for p in paths)
    by_path = {f["path"]: f for f in items[0]["files"]}
    assert by_path["Qwen3.8-27B-UD-IQ1_S.gguf"]["companion_mb"] == 0.0
    assert by_path["Qwen3.8-27B-BF16-00001-of-00002.gguf"]["companion_mb"] == 25732.0


def test_search_hf_parallel_keeps_order(monkeypatch):
    """search_hf runs per-repo work concurrently and preserves HF order."""
    repos = [
        {"id": f"org/R{i}", "downloads": i, "likes": 0, "gated": False, "license": "apache-2.0", "private": False}
        for i in range(3)
    ]
    monkeypatch.setattr(
        model_hub, "_repo_files", lambda repo, limit=50: [{"path": "m.gguf", "size_mb": 10.0, "sha256": "a" * 64}]
    )
    monkeypatch.setattr(model_hub, "_hf_get", lambda *a, **k: _Resp(repos))
    monkeypatch.setattr(model_hub, "get_repo_arch", lambda repo: None)
    monkeypatch.setattr(model_hub, "_gguf_arch", lambda repo, path: {"block_count": 64})
    items = model_hub.search_hf("q", 5)
    assert [i["repo"] for i in items] == [f"org/R{i}" for i in range(3)]


def test_search_hf_computes_fit_when_context_given(monkeypatch):
    """With a context argument every model file carries a ready fit, and
    estimate_fit receives that context and the module derived from the type."""
    repos = [{"id": "org/A", "downloads": 1, "likes": 0, "gated": False, "license": "apache-2.0", "private": False}]
    monkeypatch.setattr(
        model_hub, "_repo_files", lambda repo, limit=50: [{"path": "m.gguf", "size_mb": 10.0, "sha256": "a" * 64}]
    )
    monkeypatch.setattr(model_hub, "_hf_get", lambda *a, **k: _Resp(repos))
    monkeypatch.setattr(model_hub, "get_repo_arch", lambda repo: {"block_count": 64})
    captured = {}
    monkeypatch.setattr(
        model_hub,
        "estimate_fit",
        lambda repo, file, module=None, context_length=8192: (
            captured.update(module=module, context_length=context_length),
            {"tier": "good", "platform": "gpu"},
        )[1],
    )
    items = model_hub.search_hf("q", 5, context_length=32768)
    assert items[0]["files"][0]["fit"]["tier"] == "good"
    assert captured["context_length"] == 32768
    assert captured["module"] == "reasoning"


def test_search_hf_without_context_skips_fit(monkeypatch):
    """Without a context argument no fit is attached (legacy/render-only path)."""
    repos = [{"id": "org/A", "downloads": 1, "likes": 0, "gated": False, "license": "apache-2.0", "private": False}]
    monkeypatch.setattr(
        model_hub, "_repo_files", lambda repo, limit=50: [{"path": "m.gguf", "size_mb": 10.0, "sha256": "a" * 64}]
    )
    monkeypatch.setattr(model_hub, "_hf_get", lambda *a, **k: _Resp(repos))
    monkeypatch.setattr(model_hub, "get_repo_arch", lambda repo: {"block_count": 64})
    monkeypatch.setattr(
        model_hub, "estimate_fit", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called"))
    )
    items = model_hub.search_hf("q", 5)
    assert "fit" not in items[0]["files"][0]


def test_arch_cache_disk_roundtrip(tmp_path, monkeypatch):
    """Arch metadata survives a restart via the JSON cache in the models dir."""
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    old_path = model_hub._arch_cache_disk_path
    old_arch = dict(model_hub._arch_cache)
    old_gguf = dict(model_hub._gguf_arch_cache)
    monkeypatch.setenv("MODELS_DIR", str(models_dir))
    try:
        model_hub._arch_cache_disk_path = None
        model_hub._arch_cache.clear()
        model_hub._gguf_arch_cache.clear()
        model_hub._arch_cache["org/A"] = {"block_count": 64}
        model_hub._gguf_arch_cache[("org/A", "m.gguf")] = {"block_count": 64}
        model_hub._gguf_arch_cache[("org/B", None)] = {"expert_count": 8}
        model_hub._persist_arch_cache()
        disk = model_hub._hub_arch_cache_path()
        assert disk and os.path.exists(disk)
        model_hub._arch_cache.clear()
        model_hub._gguf_arch_cache.clear()
        model_hub._load_arch_cache_from_disk()
        assert model_hub._arch_cache == {"org/A": {"block_count": 64}}
        assert model_hub._gguf_arch_cache == {
            ("org/A", "m.gguf"): {"block_count": 64},
            ("org/B", None): {"expert_count": 8},
        }
    finally:
        model_hub._arch_cache_disk_path = old_path
        model_hub._arch_cache.clear()
        model_hub._arch_cache.update(old_arch)
        model_hub._gguf_arch_cache.clear()
        model_hub._gguf_arch_cache.update(old_gguf)


def test_estimate_fits_skips_aux_and_maps_errors(monkeypatch):
    """estimate_fits covers only model files and surfaces per-file errors."""
    files = [
        {"path": "Q4_K_M.gguf", "size_mb": 10.0, "sha256": "a" * 64},
        {"path": "Q2_K.gguf", "size_mb": 5.0, "sha256": "b" * 64},
        {"path": "imatrix_unsloth.gguf", "size_mb": 1.0, "sha256": "c" * 64},
        {"path": "mtp-Q4_0.gguf", "size_mb": 2.0, "sha256": "d" * 64},
    ]
    monkeypatch.setattr(model_hub, "_repo_files", lambda repo, limit=50: files)

    def fake_estimate(repo, file_path, module="multimodal", context_length=8192):
        if file_path == "Q2_K.gguf":
            raise model_hub.DownloadBlocked("unknown_arch")
        return {"tier": "good", "platform": "gpu"}

    monkeypatch.setattr(model_hub, "estimate_fit", fake_estimate)
    out = model_hub.estimate_fits("org/A", context_length=32768)
    assert list(out) == ["Q4_K_M.gguf", "Q2_K.gguf"]
    assert out["Q4_K_M.gguf"]["tier"] == "good"
    assert out["Q2_K.gguf"] == {"error": "unknown_arch"}


def _gguf_metadata_header_wide_tail():
    """GGUF header whose sizing keys sit behind a ~6 MB tokenizer.merges
    array, past the old 4 MB read prefix (e.g. HauhauCS Qwen3.8-27B)."""
    merges = [_gguf_string(f"abc{chr(ord('a') + i % 26) * 96}") for i in range(60000)]
    fields = [
        ("general.architecture", 8, _gguf_string("qwen35")),
        ("tokenizer.merges", 9, struct.pack("<IQ", 8, len(merges)) + b"".join(merges)),
        ("qwen35.block_count", 4, struct.pack("<I", 32)),
        ("qwen35.context_length", 4, struct.pack("<I", 262144)),
        ("qwen35.expert_count", 4, struct.pack("<I", 8)),
    ]
    header = b"GGUF" + struct.pack("<IQQ", 3, 0, len(fields))
    return header + b"".join(_gguf_string(key) + struct.pack("<I", kind) + value for key, kind, value in fields)


def test_estimate_fit_grows_gguf_head_past_4mb(monkeypatch):
    monkeypatch.setattr(model_hub, "_gguf_arch_cache", {}, raising=False)
    full = _gguf_metadata_header_wide_tail()
    assert len(full) > 6 * 1024 * 1024
    files = [{"path": "wide.gguf", "size_mb": 2400, "sha256": "a" * 64}]
    range_ends = []

    class _WideResp:
        status_code = 206
        url = "https://huggingface.co/org/Wide/resolve/main/wide.gguf"

        def __init__(self, end):
            self.end = end

        def iter_content(self, chunk_size):
            yield full[: self.end + 1]

    def fake_hf_get(url, params=None, stream=False, extra_headers=None, timeout=30):
        end = int(extra_headers["Range"].split("-")[1])
        range_ends.append(end)
        return _WideResp(end)

    monkeypatch.setattr(model_hub, "_repo_files", lambda repo, limit=50: files)
    monkeypatch.setattr(model_hub, "get_repo_arch", lambda repo: None)
    monkeypatch.setattr(model_hub, "_hf_get", fake_hf_get)
    monkeypatch.setattr(
        model_hub,
        "_classify_model_fit",
        lambda *args, **kwargs: {"tier": "good", "arch_max_ctx": 0},
    )

    fit = model_hub.estimate_fit("org/Wide", "wide.gguf")
    assert fit["block_count"] == 32
    assert fit["expert_count"] == 8
    assert fit["arch"] == "qwen35"
    assert max(range_ends) > 8 * 1024 * 1024


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


def test_delete_installed_removes_marker_companions(tmp_path, monkeypatch):
    """Deleting a Hub-downloaded model removes its marker companions too."""
    (tmp_path / "My.Q4_K_M.gguf").write_bytes(b"m")
    (tmp_path / "mtp-My.Q4_K_M.gguf").write_bytes(b"c")
    (tmp_path / "My.Q4_K_M.hubmeta").write_text(
        '{"repo": "org/My", "file": "pooled/My.Q4_K_M.gguf", "names": ["My.Q4_K_M.gguf", "mtp-My.Q4_K_M.gguf"]}',
        encoding="utf-8",
    )
    cache_drops = []

    def fake_remove(names):
        cache_drops.extend(names)

    monkeypatch.setattr("app.utils.remove_gguf_cache_entries", fake_remove)
    result = model_hub.delete_installed("My.Q4_K_M.gguf", models_dir=str(tmp_path))
    assert result["removed"] == ["My.Q4_K_M.gguf", "mtp-My.Q4_K_M.gguf"]
    assert result["from_hub"] is True
    assert not (tmp_path / "My.Q4_K_M.gguf").exists()
    assert not (tmp_path / "mtp-My.Q4_K_M.gguf").exists()
    assert not (tmp_path / "My.Q4_K_M.hubmeta").exists()
    assert cache_drops == ["My.Q4_K_M", "mtp-My.Q4_K_M"]


def test_delete_installed_without_marker_deletes_single_file(tmp_path, monkeypatch):
    """A manually placed model (no marker) is deleted as one file."""
    (tmp_path / "manual.Q5_K_M.gguf").write_bytes(b"x")
    monkeypatch.setattr("app.utils.remove_gguf_cache_entries", lambda names: None)
    result = model_hub.delete_installed("manual.Q5_K_M.gguf", models_dir=str(tmp_path))
    assert result["removed"] == ["manual.Q5_K_M.gguf"]
    assert result["from_hub"] is False
    assert not (tmp_path / "manual.Q5_K_M.gguf").exists()


def test_delete_installed_rejects_traversal(tmp_path):
    for filename in ("../evil.gguf", "sub/evil.gguf", "evil.txt"):
        with pytest.raises(model_hub.DownloadBlocked):
            model_hub.delete_installed(filename, models_dir=str(tmp_path))


def test_list_installed_returns_type_and_sorts(tmp_path):
    """list_installed() must classify each file (reasoning/multimodal/embedding)
    and sort by type first, then by file size ascending."""
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    files = {
        "qwen3.6-35b-MTP.gguf": b"a" * int(1.5 * 1024 * 1024),  # reasoning (default)
        "gpt-oss-20b-Q8.gguf": b"b" * int(7 * 1024 * 1024),  # reasoning
        "qwen3vl-8b-Q4.gguf": b"c" * int(2.5 * 1024 * 1024),  # multimodal (vl)
        "bge-m3-Q8.gguf": b"d" * int(1 * 1024 * 1024),  # embedding (bge)
    }
    for name, data in files.items():
        (models_dir / name).write_bytes(data)

    out = model_hub.list_installed(models_dir=str(models_dir))

    assert [f["name"] for f in out] == [
        "qwen3.6-35b-MTP.gguf",  # reasoning, smaller first
        "gpt-oss-20b-Q8.gguf",  # reasoning
        "qwen3vl-8b-Q4.gguf",  # multimodal
        "bge-m3-Q8.gguf",  # embedding
    ]
    assert [f["type"] for f in out] == ["reasoning", "reasoning", "multimodal", "embedding"]
    assert [f["size_mb"] for f in out] == [1.5, 7.0, 2.5, 1.0]
