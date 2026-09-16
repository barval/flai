# tests/test_autofit_context.py
"""Tests for the v11.4 context-window auto-fit used by the GPU seed.

Goals are tiered by VRAM (see _GPU_CTX_GOALS in app/database.py):
   24 GB   — multimodal 32768 / reasoning 32768
   16 GB   — multimodal 32768 / reasoning 24576
   8–12 GB — multimodal 16384 / reasoning 16384
The chosen window is additionally capped by the model's architectural max
(arch_max_ctx from GGUF metadata) and stepped down when the full-GPU VRAM
footprint of a large window does not fit.
"""

from unittest.mock import patch

from app.database import _GPU_CTX_FALLBACK, _GPU_CTX_GOALS, _autofit_context

# Qwen3.6-35B-A3B-UD-Q2_K_XL: ~11.4 GiB, MoE (128 experts), arch max 32768.
REASONING_META = {
    "arch_max_ctx": 32768,
    "file_size_mb": 11715,  # ~11.44 GiB
    "block_count": 32,
    "expert_count": 128,
}
# Qwen3VL-8B-Instruct-Q4_K_M: ~4.7 GiB dense, big arch max.
MULTIMODAL_META = {
    "arch_max_ctx": 131072,
    "file_size_mb": 4795,
    "block_count": 40,
    "expert_count": 0,
}


def _autofit(
    module: str,
    goal: int,
    vram_mb: int,
    ram_mb: int,
    meta: dict | None = None,
    model_name: str = "test-model",
) -> int:
    return _autofit_context(module, model_name, goal, vram_mb, ram_mb, meta or {})


class TestTierGoals:
    """Tier goals from _GPU_CTX_GOALS stay in sync with the docstring."""

    def test_goals_ordered_by_vram(self):
        mins = [t[0] for t in _GPU_CTX_GOALS]
        assert mins == sorted(mins, reverse=True)

    def test_multimodal_floor_is_16384(self):
        # Smallest multimodal goal across tiers must be at least the vision floor.
        assert min(t[1] for t in _GPU_CTX_GOALS) >= 16384

    def test_fallback_present(self):
        assert _GPU_CTX_FALLBACK == (32768, 24576)


class TestReasoningAutofit:
    """Reasoning window selection per VRAM tier."""

    def test_24gb_gets_32768(self):
        ctx = _autofit("reasoning", 32768, 24564, 32768, REASONING_META)
        assert ctx == 32768

    def test_16gb_gets_24576_and_rejects_32768(self):
        # 32768 would spill past the 85% VRAM budget on 16 GB.
        ctx = _autofit("reasoning", 32768, 16311, 24576, REASONING_META)
        assert ctx == 24576
        # The 16 GB tier goal is already 24576.
        ctx2 = _autofit("reasoning", 24576, 16311, 24576, REASONING_META)
        assert ctx2 == 24576

    def test_8gb_keeps_tier_goal_when_nothing_fits_fully(self):
        # The model body (~11.4 GiB) never fits an 8 GB card fully, so the
        # tier goal (16384) is kept and normal ngl-degradation applies.
        ctx = _autofit("reasoning", 16384, 8192, 16384, REASONING_META)
        assert ctx == 16384

    def test_12gb_keeps_tier_goal(self):
        ctx = _autofit("reasoning", 16384, 12288, 24576, REASONING_META)
        assert ctx == 16384


class TestMultimodalAutofit:
    """Multimodal window selection per VRAM tier."""

    def test_24gb_gets_32768(self):
        ctx = _autofit("multimodal", 32768, 24564, 32768, MULTIMODAL_META)
        assert ctx == 32768

    def test_16gb_gets_32768(self):
        # 4.7 GiB weights + mmproj + KV at 32768 fits 16 GB comfortably.
        ctx = _autofit("multimodal", 32768, 16311, 24576, MULTIMODAL_META)
        assert ctx == 32768

    def test_8gb_gets_16384(self):
        ctx = _autofit("multimodal", 16384, 8192, 16384, MULTIMODAL_META)
        assert ctx == 16384


class TestArchMaxCaps:
    """Window never exceeds the model's architectural max context."""

    def test_small_arch_max_caps_window(self):
        small = {"arch_max_ctx": 4096, "file_size_mb": 4000, "block_count": 32}
        ctx = _autofit("multimodal", 32768, 24564, 32768, small)
        assert ctx == 4096

    def test_arch_max_below_vision_floor_uses_arch_max(self):
        tiny = {"arch_max_ctx": 8192, "file_size_mb": 3000, "block_count": 32}
        ctx = _autofit("multimodal", 16384, 8192, 16384, tiny)
        assert ctx == 8192

    def test_reasoning_arch_max_caps(self):
        small = {"arch_max_ctx": 8192, "file_size_mb": 6000, "block_count": 32}
        ctx = _autofit("reasoning", 24576, 16311, 24576, small)
        assert ctx == 8192


class TestFallbacks:
    """Behaviour when GGUF metadata or VRAM info is unavailable."""

    def test_no_metadata_returns_goal(self):
        assert _autofit("multimodal", 32768, 16311, 16384) == 32768
        assert _autofit("reasoning", 24576, 16311, 24576) == 24576

    def test_no_vram_returns_goal(self):
        ctx = _autofit("reasoning", 24576, 0, 16384, REASONING_META)
        assert ctx == 24576

    @patch("app.utils.get_mmproj_size_mb")
    def test_low_ram_keeps_goal(self, mock_mmproj):
        mock_mmproj.return_value = 0
        # Weight-only footprint already exceeds the RAM budget — degradation
        # handles it, so the window goal is preserved.
        huge = {"arch_max_ctx": 131072, "file_size_mb": 20000, "block_count": 80}
        ctx = _autofit("reasoning", 16384, 24564, 16384, huge)
        assert ctx == 16384
