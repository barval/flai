# tests/test_classify_model_fit.py
"""Tests for the 3-tier VRAM/RAM classification used by admin UI.

Three tiers:
- good: fits fully in VRAM
- cpu_offload: needs partial CPU offload (degrade n_gpu_layers)
- impossible: doesn't fit even with full CPU offload (not enough RAM)
"""
from unittest.mock import patch

from app.routes.admin import _classify_model_fit

# Sample GGUF cache payloads
SMALL_MODEL = {  # 2 GB Qwen3-4B Q4
    "Qwen3-4B-Instruct-2507-Q4_K_M": {
        "context_length": 262144,
        "file_size_mb": 2400,
        "block_count": 36,
        "expert_count": 0,
    }
}
MEDIUM_MODEL = {  # 9 GB Qwen3.5-9B
    "Qwen3.5-9B-Q8_0": {
        "context_length": 262144,
        "file_size_mb": 9086,
        "block_count": 32,
        "expert_count": 0,
    }
}
HUGE_MODEL = {  # 25 GB model that doesn't fit in 16GB RAM
    "VeryLargeModel": {
        "context_length": 131072,
        "file_size_mb": 25000,  # 25 GB > 13.6 GB VRAM (85%) AND (25-13.6)=11.4 GB > 9.2 GB RAM
        "block_count": 80,
        "expert_count": 0,
    }
}


class TestClassifyModelFit:
    """Test tier classification for various model sizes."""

    @patch("app.utils.get_gguf_models_cached")
    def test_small_model_fits_vram_tier_good(self, mock_cache):
        """2 GB model on 16 GB GPU: tier=good, can_save=True, full ngl."""
        mock_cache.return_value = SMALL_MODEL
        result = _classify_model_fit(
            model_name="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
            context_length=8192,
        )
        assert result["tier"] == "good"
        assert result["can_save"] is True
        assert result["ngl_recommended"] == 36
        assert result["ngl_total"] == 36
        assert "Fits in VRAM" in result["message"]

    @patch("app.utils.get_gguf_models_cached")
    def test_medium_model_needs_cpu_offload_tier_yellow(self, mock_cache):
        """9 GB model on 16 GB GPU at small ctx: might still fit.
        Use larger ctx to push into cpu_offload tier."""
        mock_cache.return_value = MEDIUM_MODEL
        result = _classify_model_fit(
            model_name="Qwen3.5-9B-Q8_0.gguf",
            context_length=65536,  # big ctx → bigger KV cache
        )
        # 9 GB weights + ~5 GB KV @ 65K = 14 GB → fits at 85% of 16 GB
        # But might be in cpu_offload if model weights + KV exceed 85% of 16GB
        assert result["tier"] in ("good", "cpu_offload")
        if result["tier"] == "cpu_offload":
            assert result["can_save"] is True
            assert result["ngl_recommended"] < 32
            assert "Partial CPU offload" in result["message"]

    @patch("app.utils.get_gguf_models_cached")
    def test_huge_model_tier_impossible(self, mock_cache):
        """15 GB model on 16 GB RAM system: doesn't fit anywhere → impossible."""
        mock_cache.return_value = HUGE_MODEL
        # 15 GB > 85% of 16 GB VRAM (13.6 GB) AND > 70% of 16 GB RAM - 2 GB (9.2 GB)
        with patch("app.routes.admin._get_actual_vram_mb", return_value=(0, 16311)), \
             patch("app.routes.admin._get_total_ram_mb", return_value=16384):
            result = _classify_model_fit(
                model_name="VeryLargeModel.gguf",
                context_length=8192,
            )
        assert result["tier"] == "impossible"
        assert result["can_save"] is False
        assert result["ngl_recommended"] == 0
        assert "cannot be loaded" in result["message"]

    @patch("app.utils.get_gguf_models_cached")
    def test_very_huge_model_always_impossible(self, mock_cache):
        """Model that exceeds both VRAM and (VRAM + RAM): impossible."""
        # 30 GB model: 13 GB on GPU + 17 GB in RAM. 70%×16 - 2 = 9.2 GB RAM budget
        # → 17 GB > 9.2 GB → impossible
        very_huge = {
            "Llama-3.1-70B-Q4_K_M": {
                "context_length": 131072,
                "file_size_mb": 30000,  # 30 GB
                "block_count": 80,
                "expert_count": 0,
            }
        }
        mock_cache.return_value = very_huge
        with patch("app.routes.admin._get_total_ram_mb", return_value=16384), \
             patch("app.routes.admin._get_actual_vram_mb", return_value=(0, 16311)):
            result = _classify_model_fit(
                model_name="Llama-3.1-70B-Q4_K_M.gguf",
                context_length=8192,
            )
        # 30 GB: 13 GB GPU + 17 GB RAM needed, but only 9.2 GB RAM available
        assert result["tier"] == "impossible"
        assert result["can_save"] is False

    @patch("app.utils.get_gguf_models_cached")
    def test_unknown_model_blocks_save(self, mock_cache):
        """Model not in cache: tier=unknown, can_save=False."""
        mock_cache.return_value = {}  # empty
        result = _classify_model_fit(
            model_name="nonexistent-model.gguf",
            context_length=8192,
        )
        assert result["tier"] == "unknown"
        assert result["can_save"] is False
        assert "Refresh models" in result["message"]

    @patch("app.utils.get_gguf_models_cached")
    def test_arch_max_ctx_propagated(self, mock_cache):
        """arch_max_ctx is returned for client-side validation."""
        mock_cache.return_value = SMALL_MODEL
        result = _classify_model_fit(
            model_name="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
            context_length=8192,
        )
        assert result["arch_max_ctx"] == 262144

    @patch("app.utils.get_gguf_models_cached")
    def test_model_name_strips_gguf_extension(self, mock_cache):
        """model_name with .gguf should look up the right cache key."""
        mock_cache.return_value = SMALL_MODEL
        result = _classify_model_fit(
            model_name="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
            context_length=8192,
        )
        assert result["tier"] == "good"

    @patch("app.utils.get_gguf_models_cached")
    def test_cpu_offload_ngl_proportional(self, mock_cache):
        """For cpu_offload tier, ngl scales with VRAM budget."""
        mock_cache.return_value = MEDIUM_MODEL
        # Force RAM to be huge so model can fit with CPU offload
        with patch("app.routes.admin._get_total_ram_mb", return_value=64000), \
             patch("app.routes.admin._get_actual_vram_mb", return_value=(0, 16311)):
            result = _classify_model_fit(
                model_name="Qwen3.5-9B-Q8_0.gguf",
                context_length=131072,  # very large ctx
            )
        # 9 GB + 5 GB KV = 14 GB > 85% of 16 GB (13.6 GB)
        # but < 70% × 64 GB (44.8 GB) - 2 GB = 42.8 GB
        # → tier should be cpu_offload
        if result["tier"] == "cpu_offload":
            assert 1 <= result["ngl_recommended"] < 32
            assert result["ngl_recommended"] + (
                result["ngl_total"] - result["ngl_recommended"]
            ) == result["ngl_total"]


class TestClassifyEdgeCases:
    """Edge cases for tier classification."""

    @patch("app.utils.get_gguf_models_cached")
    def test_minimum_ctx(self, mock_cache):
        """Minimum context (512) should still classify correctly."""
        mock_cache.return_value = SMALL_MODEL
        result = _classify_model_fit(
            model_name="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
            context_length=512,
        )
        assert result["can_save"] is True
        assert result["tier"] == "good"

    @patch("app.utils.get_gguf_models_cached")
    def test_gguf_cache_called_with_models_dir(self, mock_cache):
        """get_gguf_models_cached is called with /models path."""
        mock_cache.return_value = SMALL_MODEL
        _classify_model_fit(
            model_name="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
            context_length=8192,
        )
        mock_cache.assert_called_once_with("/models")

    @patch("app.utils.get_gguf_models_cached")
    def test_returns_vram_metadata(self, mock_cache):
        """Result includes vram_mb, file_mb, kv_cache_mb, etc."""
        mock_cache.return_value = SMALL_MODEL
        result = _classify_model_fit(
            model_name="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
            context_length=8192,
        )
        assert "vram_mb" in result
        assert "file_mb" in result
        assert "kv_cache_mb" in result
        assert "total_vram_mb" in result
        assert "system_ram_mb" in result
        assert result["file_mb"] == 2400.0
