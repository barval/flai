"""LTX-Video wrapper offline-integrity tests.

Regression: `ensure_pipeline()` resolved the spatial upscaler through
`resolve_model_path()`, which falls back to a HuggingFace download when the
file is missing locally. That made the ltxvideo container reach out to the
internet on every pipeline initialization — violating the project rule that
FLAI must run fully offline after deploy. The upscaler is only ever needed
for the `multi-scale` pipeline type, which FLAI does not use.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WRAPPER = REPO / "services" / "ltx_video" / "ltx_wrapper.py"
SOURCE = WRAPPER.read_text(encoding="utf-8")


def test_spatial_upscaler_resolved_from_local_dir_only():
    """The upscaler must be picked up from the local models dir, never downloaded."""
    start = SOURCE.index("spatial_upscaler = config.get")
    end = SOURCE.index("precision = config.get", start)
    block = SOURCE[start:end]
    assert "(Path(MODELS_DIR) / spatial_upscaler).exists()" in block, (
        "upscaler resolution must gate on a local file check"
    )
    assert "resolve_model_path" not in block, (
        "upscaler must not go through resolve_model_path (it may download from HuggingFace)"
    )
    assert "hf_hub_download" not in block, "upscaler block must never trigger a HuggingFace download"
