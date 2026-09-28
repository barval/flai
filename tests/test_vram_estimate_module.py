# tests/test_vram_estimate_module.py
"""VRAM helpers were moved out of app/routes/admin.py into app/vram_estimate.py.

The admin module re-exports them so existing callers keep working.
"""

from unittest.mock import patch


def test_classify_is_re_exported_by_admin():
    from app.routes.admin import _classify_model_fit as admin_classify
    from app.vram_estimate import _classify_model_fit as vram_classify

    assert admin_classify is vram_classify


def test_classify_unknown_without_metadata():
    from app.vram_estimate import _classify_model_fit

    with (
        patch("app.utils.get_gguf_models_cached", return_value={}),
        patch("app.vram_estimate.os.path.exists", return_value=False),
    ):
        res = _classify_model_fit("Nope-Model", 8192, file_size_mb=None, block_count=None, module="multimodal")
    assert res["tier"] == "unknown"
    assert res["can_save"] is False


def test_estimate_vram_returns_fields():
    from app.vram_estimate import _estimate_model_vram

    est = _estimate_model_vram(file_size_mb=2400, block_count=36, ngl=36, ctx_size=8192)
    assert est["total_mb"] > est["model_vram_mb"] > 0


def _classify(
    monkeypatch,
    *,
    vram,
    free_ram,
    free_ram_running=0,
    total_ram=32768,
    file_mb=2400,
    blocks=36,
    ctx=8192,
    module="reasoning",
):
    """Drive _classify_model_fit with controlled VRAM/RAM and patched cache."""
    from app import vram_estimate

    monkeypatch.setattr(vram_estimate, "_get_actual_vram_mb", lambda: vram)
    monkeypatch.setattr(vram_estimate, "_get_free_ram_mb", lambda: free_ram)
    monkeypatch.setattr(vram_estimate, "_get_llama_swap_running_ram_mb", lambda: free_ram_running)
    monkeypatch.setattr(vram_estimate, "_get_total_ram_mb", lambda: total_ram)
    monkeypatch.setattr("app.utils.get_gguf_models_cached", lambda path: {})
    monkeypatch.setattr("app.utils.get_mmproj_size_mb", lambda name: 0)
    monkeypatch.setattr(vram_estimate, "_find_gguf_path", lambda name: None)
    monkeypatch.setattr(vram_estimate.os.path, "exists", lambda p: False)
    return vram_estimate._classify_model_fit(
        model_name=f"Test-{file_mb}MB-{ctx}ctx",
        context_length=ctx,
        file_size_mb=file_mb,
        block_count=blocks,
        module=module,
    )


def test_classify_gpu_full_fit_is_good(monkeypatch):
    res = _classify(monkeypatch, vram=(0, 8192), free_ram=24576, file_mb=2400, blocks=36, ctx=8192)
    assert res["tier"] == "good"
    assert res["platform"] == "gpu"
    assert res["free_ram_mb"] == 24576


def test_classify_gpu_offload_when_vram_insufficient(monkeypatch):
    res = _classify(monkeypatch, vram=(0, 4096), free_ram=24576, file_mb=4000, blocks=36, ctx=8192)
    assert res["tier"] == "cpu_offload"
    assert res["platform"] == "gpu"


def test_classify_gpu_impossible_when_ram_insufficient(monkeypatch):
    res = _classify(monkeypatch, vram=(0, 4096), free_ram=1024, file_mb=4000, blocks=36, ctx=8192)
    assert res["tier"] == "impossible"
    assert res["platform"] == "gpu"


def test_classify_cpu_never_good(monkeypatch):
    res = _classify(monkeypatch, vram=(None, None), free_ram=24576, file_mb=2400, blocks=36, ctx=8192)
    assert res["platform"] == "cpu"
    assert res["tier"] != "good"


def test_classify_cpu_offload_when_ram_available(monkeypatch):
    res = _classify(monkeypatch, vram=(None, None), free_ram=12288, file_mb=2400, blocks=36, ctx=8192)
    assert res["platform"] == "cpu"
    assert res["tier"] == "cpu_offload"


def test_classify_cpu_impossible_when_ram_insufficient(monkeypatch):
    res = _classify(monkeypatch, vram=(None, None), free_ram=1024, file_mb=4000, blocks=36, ctx=8192)
    assert res["platform"] == "cpu"
    assert res["tier"] == "impossible"


def test_classify_running_models_ram_counts_as_free(monkeypatch):
    # A model currently resident via llama-swap is replaced when a new one is
    # chosen, so its RAM is treated as free: free_ram 1024 + running 8192
    # gives the same result as free_ram 9216 with nothing running.
    res_running = _classify(
        monkeypatch, vram=(0, 4096), free_ram=1024, free_ram_running=8192, file_mb=4000, blocks=36, ctx=8192
    )
    res_idle = _classify(
        monkeypatch, vram=(0, 4096), free_ram=9216, free_ram_running=0, file_mb=4000, blocks=36, ctx=8192
    )
    assert res_running["tier"] == res_idle["tier"] == "cpu_offload"
    assert res_running["free_ram_mb"] == res_idle["free_ram_mb"] == 9216


def test_classify_running_models_ram_only_helps_offload(monkeypatch):
    # Running-model RAM must not change the VRAM "good" decision at all.
    res = _classify(
        monkeypatch, vram=(0, 8192), free_ram=1024, free_ram_running=24576, file_mb=2400, blocks=36, ctx=8192
    )
    assert res["tier"] == "good"
    assert res["free_ram_mb"] == 25600


def test_get_llama_swap_running_ram_mb_sums_running_files(monkeypatch):
    from app import vram_estimate

    monkeypatch.setattr(vram_estimate, "_swap_running_models", lambda: ["model-a.gguf", "model-b.gguf"])
    monkeypatch.setattr(
        "app.utils.get_gguf_models_cached",
        lambda path: {
            "model-a": {"file_size_mb": 2048},
            "model-b": {"file_size_mb": 3072},
            "model-c": {"file_size_mb": 1111},
        },
    )
    assert vram_estimate._get_llama_swap_running_ram_mb() == 5120


def test_get_llama_swap_running_ram_mb_handles_no_models(monkeypatch):
    from app import vram_estimate

    monkeypatch.setattr(vram_estimate, "_swap_running_models", lambda: [])
    monkeypatch.setattr("app.utils.get_gguf_models_cached", lambda path: {})
    assert vram_estimate._get_llama_swap_running_ram_mb() == 0


def test_swap_running_models_parses_dict_cmd(monkeypatch):
    import app.vram_estimate as ve

    fake_resp = {"running": [{"name": "multimodal", "cmd": "llama-server -m /models/X/Y.gguf --ctx 4096"}]}
    monkeypatch.setattr(ve, "requests", _FakeRequests(fake_resp))
    assert ve._swap_running_models() == ["Y.gguf"]


def test_swap_running_models_parses_str_list(monkeypatch):
    import app.vram_estimate as ve

    fake_resp = {"running": ["A.gguf", "B.gguf"]}
    monkeypatch.setattr(ve, "requests", _FakeRequests(fake_resp))
    assert ve._swap_running_models() == ["A.gguf", "B.gguf"]


class _FakeRequests:
    """Minimal requests stub returning a fixed JSON payload."""

    def __init__(self, payload):
        self._payload = payload

    def get(self, url, timeout=0):
        class _Resp:
            status_code = 200

            def json(self):
                return self._payload

            def __init__(self, payload):
                self._payload = payload

        return _Resp(self._payload)


def test_classify_unknown_includes_platform_and_free_ram(monkeypatch):
    res = _classify(monkeypatch, vram=(0, 8192), free_ram=24576, file_mb=None, blocks=None, ctx=8192)
    assert res["tier"] == "unknown"
    assert res["platform"] in ("gpu", "cpu")
