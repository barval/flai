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
