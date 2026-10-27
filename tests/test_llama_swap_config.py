"""Tests for llama-swap configuration generator."""

import os
import tempfile
from unittest.mock import MagicMock, patch

from app.llama_swap_config import (
    DEFAULT_TTL,
    LlamaSwapConfigGenerator,
    generate_and_write,
)
from app.utils import reasoning_budget_tokens


def _mock_config(module, **overrides):
    configs = {
        "embedding": {
            "module": "embedding",
            "model_name": "bge-m3-Q8_0",
            "context_length": 512,
            "timeout": 120,
            "service_url": "http://flai-llamacpp:8033",
            "ttl": None,
            "model_path": None,
            "aliases": None,
        },
        "reasoning": {
            "module": "reasoning",
            "model_name": "gpt-oss-20b-mxfp4",
            "context_length": 8192,
            "temperature": 0.7,
            "timeout": 120,
            "service_url": "http://flai-llamacpp:8033",
            "ttl": None,
            "model_path": None,
            "aliases": None,
        },
        "multimodal": {
            "module": "multimodal",
            "model_name": "Qwen3VL-8B-Instruct-Q4_K_M",
            "context_length": 8192,
            "temperature": 0.7,
            "timeout": 120,
            "service_url": "http://flai-llamacpp:8033",
            "ttl": None,
            "model_path": None,
            "aliases": None,
        },
    }
    base = configs.get(module, {})
    base.update(overrides)
    return base


class TestRerankerEntry:
    """The reranker follows the admin's model choice, not a hardcoded file."""

    @patch("app.llama_swap_config.get_model_config")
    @patch("app.llama_swap_config.os.path.exists")
    def test_entry_uses_the_configured_model(self, mock_exists, mock_get_config):
        mock_get_config.return_value = {
            "module": "reranker",
            "model_name": "my-cross-encoder-Q4_K_M",
            "context_length": 1024,
        }
        mock_exists.side_effect = lambda p: p == "/models/my-cross-encoder-Q4_K_M.gguf"

        entry = LlamaSwapConfigGenerator().build_reranker_entry()

        assert entry is not None
        cmd = entry["reranker"]["cmd"]
        assert "-m /models/my-cross-encoder-Q4_K_M.gguf" in cmd
        assert "--ctx-size 1024" in cmd, "the configured window is used"
        assert "--n-gpu-layers 0" in cmd, "the reranker stays on the CPU"

    @patch("app.llama_swap_config.get_model_config")
    @patch("app.llama_swap_config.os.path.exists")
    def test_legacy_path_is_the_fallback(self, mock_exists, mock_get_config):
        mock_get_config.return_value = {"module": "reranker", "model_name": None, "context_length": 2048}
        mock_exists.side_effect = lambda p: p == LlamaSwapConfigGenerator.RERANKER_PATH

        entry = LlamaSwapConfigGenerator().build_reranker_entry()

        assert entry is not None
        assert LlamaSwapConfigGenerator.RERANKER_PATH in entry["reranker"]["cmd"]

    @patch("app.llama_swap_config.get_model_config")
    @patch("app.llama_swap_config.os.path.exists")
    def test_no_file_means_no_entry(self, mock_exists, mock_get_config):
        mock_get_config.return_value = {"module": "reranker", "model_name": "missing", "context_length": 2048}
        mock_exists.return_value = False

        assert LlamaSwapConfigGenerator().build_reranker_entry() is None


class TestGetModelPath:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()

    def teardown_method(self):
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("app.llama_swap_config.get_model_config")
    def test_no_config(self, mock_get_config):
        mock_get_config.return_value = None
        gen = LlamaSwapConfigGenerator()
        path = gen.get_model_path("multimodal", "test-model")
        assert path is None

    @patch("app.llama_swap_config.get_model_config")
    def test_model_path_in_config(self, mock_get_config):
        mock_get_config.return_value = _mock_config("multimodal", model_path="/models/custom.gguf")
        gen = LlamaSwapConfigGenerator()
        path = gen.get_model_path("multimodal", "Qwen3-4B-Instruct-2507-Q4_K_M")
        assert path == "/models/custom.gguf"

    @patch("app.llama_swap_config.get_model_config")
    def test_model_path_relative(self, mock_get_config):
        mock_get_config.return_value = _mock_config("multimodal", model_path="subdir/model.gguf")
        gen = LlamaSwapConfigGenerator()
        path = gen.get_model_path("multimodal", "Qwen3-4B-Instruct-2507-Q4_K_M")
        assert "/models/subdir/model.gguf" in path

    @patch("app.llama_swap_config.get_model_config")
    @patch("app.llama_swap_config.os.path.exists")
    def test_direct_gguf(self, mock_exists, mock_get_config):
        mock_get_config.return_value = _mock_config("multimodal", model_path=None)

        def exists_side_effect(p):
            return p == "/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"

        mock_exists.side_effect = exists_side_effect

        gen = LlamaSwapConfigGenerator()
        path = gen.get_model_path("multimodal", "Qwen3-4B-Instruct-2507-Q4_K_M")
        assert path == "/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"

    @patch("app.llama_swap_config.get_model_config")
    @patch("app.llama_swap_config.os.path.exists")
    def test_gguf_extension_stripping(self, mock_exists, mock_get_config):
        mock_get_config.return_value = _mock_config("multimodal", model_path=None)

        def exists_side_effect(p):
            return p == "/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"

        mock_exists.side_effect = exists_side_effect

        gen = LlamaSwapConfigGenerator()
        path = gen.get_model_path("multimodal", "Qwen3-4B-Instruct-2507-Q4_K_M.gguf")
        assert path == "/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"


class TestGetTtl:
    @patch("app.llama_swap_config.get_model_config")
    def test_default_ttl(self, mock_get_config):
        mock_get_config.return_value = None
        gen = LlamaSwapConfigGenerator()
        assert gen.get_ttl("multimodal") == DEFAULT_TTL["multimodal"]
        assert gen.get_ttl("unknown") == 300

    @patch("app.llama_swap_config.get_model_config")
    def test_custom_ttl(self, mock_get_config):
        mock_get_config.return_value = {"ttl": 999}
        gen = LlamaSwapConfigGenerator()
        assert gen.get_ttl("multimodal") == 999


class TestGetCtxSize:
    @patch("app.llama_swap_config.get_model_config")
    def test_no_config(self, mock_get_config):
        mock_get_config.return_value = None
        gen = LlamaSwapConfigGenerator()
        assert gen.get_ctx_size("multimodal") == 4096
        assert gen.get_ctx_size("reasoning") == 8192

    @patch("app.llama_swap_config.get_model_config")
    def test_config_ctx(self, mock_get_config):
        mock_get_config.return_value = {"context_length": 16384}
        gen = LlamaSwapConfigGenerator()
        assert gen.get_ctx_size("multimodal") == 16384

    @patch("app.llama_swap_config.get_model_config")
    def test_none_ctx_uses_default(self, mock_get_config):
        mock_get_config.return_value = {"context_length": None}
        gen = LlamaSwapConfigGenerator()
        assert gen.get_ctx_size("embedding") == 2048
        assert gen.get_ctx_size("multimodal") == 8192


class TestBuildModelEntry:
    @patch("app.llama_swap_config.get_model_config")
    def test_no_config(self, mock_get_config):
        mock_get_config.return_value = None
        gen = LlamaSwapConfigGenerator()
        assert gen.build_model_entry("multimodal") is None

    @patch("app.llama_swap_config.get_model_config")
    def test_no_model_name(self, mock_get_config):
        mock_get_config.return_value = {"model_name": ""}
        gen = LlamaSwapConfigGenerator()
        assert gen.build_model_entry("multimodal") is None

    @patch("app.llama_swap_config.get_model_config")
    @patch("app.llama_swap_config.os.path.exists")
    def test_full_entry(self, mock_exists, mock_get_config):
        def config_side_effect(module):
            return _mock_config(module, model_path=None)

        mock_get_config.side_effect = config_side_effect
        mock_exists.return_value = True

        gen = LlamaSwapConfigGenerator()
        entry = gen.build_model_entry("multimodal")
        assert entry is not None
        assert "multimodal" in entry
        assert "cmd" in entry["multimodal"]
        assert "ttl" in entry["multimodal"]
        assert "aliases" in entry["multimodal"]
        assert "preload" not in entry["multimodal"]

    @patch("app.llama_swap_config.get_model_config")
    @patch("app.llama_swap_config.os.path.exists")
    def test_entry_with_group(self, mock_exists, mock_get_config):
        def config_side_effect(module):
            return _mock_config(module)

        mock_get_config.side_effect = config_side_effect
        mock_exists.return_value = True

        gen = LlamaSwapConfigGenerator()
        entry = gen.build_model_entry("multimodal")
        assert entry["multimodal"].get("group") == "llm_fast"
        assert "preload" not in entry["multimodal"]

    @patch("app.llama_swap_config.get_model_config")
    @patch("app.llama_swap_config.os.path.exists")
    def test_no_group_for_default(self, mock_exists, mock_get_config):
        def config_side_effect(module):
            return _mock_config(module)

        mock_get_config.side_effect = config_side_effect
        mock_exists.return_value = True

        gen = LlamaSwapConfigGenerator()
        entry = gen.build_model_entry("multimodal")
        assert entry["multimodal"].get("group") == "llm_fast"


class TestBuildCmd:
    @patch("app.resource_manager.get_resource_manager")
    @patch("app.llama_swap_config.get_model_config")
    def test_basic_cmd(self, mock_get_config, mock_get_rm):
        mock_get_config.return_value = {"context_length": 8192}
        mock_rm = MagicMock()
        mock_rm.compute_llamacpp_config.return_value = {
            "flash_attn": True,
            "n_gpu_layers": -1,
            "offload_kqv": False,
        }
        mock_get_rm.return_value = mock_rm
        gen = LlamaSwapConfigGenerator()
        cmd = gen.build_cmd("multimodal", "/models/test.gguf")
        assert cmd.startswith("llama-server")
        assert "--port ${PORT}" in cmd
        assert "-m /models/test.gguf" in cmd
        assert "--ctx-size 8192" in cmd
        assert "--flash-attn on" in cmd
        assert "--n-gpu-layers" not in cmd
        assert "--kv-offload" not in cmd

    @patch("app.resource_manager.get_resource_manager")
    @patch("app.llama_swap_config.get_model_config")
    def test_basic_cmd_no_flash_attn(self, mock_get_config, mock_get_rm):
        mock_get_config.return_value = {"context_length": 8192}
        mock_rm = MagicMock()
        mock_rm.compute_llamacpp_config.return_value = {
            "flash_attn": False,
            "n_gpu_layers": -1,
            "offload_kqv": False,
        }
        mock_get_rm.return_value = mock_rm
        gen = LlamaSwapConfigGenerator()
        cmd = gen.build_cmd("multimodal", "/models/test.gguf")
        assert "--flash-attn" not in cmd

    @patch("app.resource_manager.get_resource_manager")
    @patch("app.llama_swap_config.get_model_config")
    def test_basic_cmd_partial_offload(self, mock_get_config, mock_get_rm):
        mock_get_config.return_value = {"context_length": 8192}
        mock_rm = MagicMock()
        mock_rm.compute_llamacpp_config.return_value = {
            "flash_attn": True,
            "n_gpu_layers": 10,
            "offload_kqv": True,
        }
        mock_get_rm.return_value = mock_rm
        gen = LlamaSwapConfigGenerator()
        cmd = gen.build_cmd("multimodal", "/models/test.gguf")
        assert "--flash-attn" not in cmd
        assert "--n-gpu-layers 10" in cmd
        assert "--kv-offload" in cmd

    @patch("app.utils.get_gguf_models_cached")
    @patch("app.resource_manager.get_resource_manager")
    @patch("app.llama_swap_config.get_model_config")
    def test_cpu_cmd_disables_gpu_only_flags(self, mock_get_config, mock_get_rm, mock_gguf):
        mock_get_config.return_value = {"context_length": 8192}
        mock_rm = MagicMock()
        mock_rm.compute_llamacpp_config.return_value = {
            "flash_attn": True,
            "n_gpu_layers": 0,
            "offload_kqv": True,
            "cache_type_k": "q4_0",
            "cache_type_v": "q4_0",
        }
        mock_get_rm.return_value = mock_rm
        mock_gguf.return_value = {"test": {"supports_mtp": True}}
        gen = LlamaSwapConfigGenerator()
        cmd = gen.build_cmd("reasoning", "/models/test.gguf")
        assert "--n-gpu-layers 0" in cmd
        assert "--flash-attn" not in cmd
        assert "--kv-offload" not in cmd
        assert "--cache-type-k" not in cmd
        assert "--cache-type-v" not in cmd
        assert "--spec-type" not in cmd

    @patch("app.resource_manager.get_resource_manager")
    @patch("app.llama_swap_config.get_model_config")
    def test_embedding_cmd(self, mock_get_config, mock_get_rm):
        mock_get_config.return_value = {"context_length": 512}
        mock_rm = MagicMock()
        mock_rm.compute_llamacpp_config.return_value = {
            "flash_attn": False,
            "n_gpu_layers": 0,
            "offload_kqv": False,
        }
        mock_get_rm.return_value = mock_rm
        gen = LlamaSwapConfigGenerator()
        cmd = gen.build_cmd("embedding", "/models/bge.gguf")
        assert "--embeddings" in cmd
        assert "--batch-size 2048" in cmd
        assert "--ubatch-size 2048" in cmd
        assert "--n-gpu-layers 0" in cmd

    @patch("app.resource_manager.get_resource_manager")
    @patch("app.llama_swap_config.get_model_config")
    def test_multimodal_cmd(self, mock_get_config, mock_get_rm):
        mock_get_config.return_value = {"context_length": 8192}
        mock_rm = MagicMock()
        mock_rm.compute_llamacpp_config.return_value = {
            "flash_attn": True,
            "n_gpu_layers": -1,
            "offload_kqv": False,
        }
        mock_get_rm.return_value = mock_rm
        gen = LlamaSwapConfigGenerator()
        cmd = gen.build_cmd("multimodal", "/models/vl.gguf", mmproj="/models/mmproj.gguf")
        assert "--mmproj /models/mmproj.gguf" in cmd
        assert "--flash-attn on" in cmd

    @patch("app.resource_manager.get_resource_manager")
    @patch("app.llama_swap_config.get_model_config")
    def test_reasoning_gpt_oss_uses_auto_format(self, mock_get_config, mock_get_rm):
        mock_get_config.return_value = {"context_length": 8192}
        mock_rm = MagicMock()
        mock_rm.compute_llamacpp_config.return_value = {
            "flash_attn": False,
            "n_gpu_layers": -1,
            "offload_kqv": False,
        }
        mock_get_rm.return_value = mock_rm
        gen = LlamaSwapConfigGenerator()
        cmd = gen.build_cmd("reasoning", "/models/gpt-oss-20b-mxfp4.gguf")
        assert "--reasoning_format auto" in cmd
        # 8192 ctx: the prompt may take 64%, so the old ctx*0.4 (3276) left
        # less than the 1500-token answer reserve.
        assert f"--reasoning-budget {reasoning_budget_tokens(8192)}" in cmd

    @patch("app.resource_manager.get_resource_manager")
    @patch("app.llama_swap_config.get_model_config")
    def test_reasoning_cmd_includes_budget(self, mock_get_config, mock_get_rm):
        mock_get_config.return_value = {"context_length": 8192}
        mock_rm = MagicMock()
        mock_rm.compute_llamacpp_config.return_value = {
            "flash_attn": False,
            "n_gpu_layers": -1,
            "offload_kqv": False,
        }
        mock_get_rm.return_value = mock_rm
        gen = LlamaSwapConfigGenerator()
        cmd = gen.build_cmd("reasoning", "/models/test.gguf")
        assert "--reasoning_format deepseek" in cmd
        # 8192 ctx: the prompt may take 64%, so the old ctx*0.4 (3276) left
        # less than the 1500-token answer reserve.
        assert f"--reasoning-budget {reasoning_budget_tokens(8192)}" in cmd

    @patch("app.resource_manager.get_resource_manager")
    @patch("app.llama_swap_config.get_model_config")
    def test_multimodal_cmd_no_budget(self, mock_get_config, mock_get_rm):
        mock_get_config.return_value = {"context_length": 8192}
        mock_rm = MagicMock()
        mock_rm.compute_llamacpp_config.return_value = {
            "flash_attn": True,
            "n_gpu_layers": -1,
            "offload_kqv": False,
        }
        mock_get_rm.return_value = mock_rm
        gen = LlamaSwapConfigGenerator()
        cmd = gen.build_cmd("multimodal", "/models/vl.gguf", mmproj="/models/mmproj.gguf")
        assert "--reasoning-budget" not in cmd

    @patch("app.resource_manager.get_resource_manager")
    @patch("app.llama_swap_config.get_model_config")
    def test_zero_ctx_size_omitted(self, mock_get_config, mock_get_rm):
        mock_get_config.return_value = {"context_length": None}
        mock_rm = MagicMock()
        mock_rm.compute_llamacpp_config.return_value = {
            "flash_attn": True,
            "n_gpu_layers": -1,
            "offload_kqv": False,
        }
        mock_get_rm.return_value = mock_rm
        gen = LlamaSwapConfigGenerator()
        cmd = gen.build_cmd("multimodal", "/models/test.gguf")
        assert "--ctx-size 8192" in cmd
        assert "--flash-attn on" in cmd


class TestGenerateYaml:
    @patch("app.llama_swap_config.get_model_config")
    @patch("app.llama_swap_config.os.path.exists")
    def test_generates_valid_yaml(self, mock_exists, mock_get_config):
        def config_side_effect(module):
            return _mock_config(module, model_path=None)

        mock_get_config.side_effect = config_side_effect
        mock_exists.return_value = True

        gen = LlamaSwapConfigGenerator()
        yaml_str = gen.generate_yaml()

        assert "logLevel: info" in yaml_str
        assert "startPort: 10001" in yaml_str
        assert "groups:" in yaml_str
        assert "llm_fast:" in yaml_str
        assert "models:" in yaml_str
        assert "embedding:" in yaml_str
        assert "reasoning:" in yaml_str
        assert "multimodal:" in yaml_str
        assert "hooks:" in yaml_str
        assert '      - "multimodal"' in yaml_str
        assert "llama-server" in yaml_str

    @patch("app.llama_swap_config.get_model_config")
    @patch("app.llama_swap_config.os.path.exists")
    def test_missing_config_skips_module(self, mock_exists, mock_get_config):
        def config_side_effect(m):
            return _mock_config(m, model_path=None) if m == "multimodal" else None

        mock_get_config.side_effect = config_side_effect
        mock_exists.return_value = True

        gen = LlamaSwapConfigGenerator()
        yaml_str = gen.generate_yaml()
        assert "multimodal:" in yaml_str
        assert "embedding:" not in yaml_str
        assert "reasoning:" not in yaml_str

    @patch("app.llama_swap_config.get_model_config")
    @patch("app.llama_swap_config.os.path.exists")
    @patch("glob.glob")
    def test_multimodal_includes_mmproj(self, mock_glob, mock_exists, mock_get_config):
        def config_side_effect(module):
            return _mock_config(module, model_path=None)

        mock_get_config.side_effect = config_side_effect
        mock_exists.return_value = True
        mock_glob.return_value = ["/models/Qwen3VL-8B-Instruct-Q4_K_M/mmproj-qwen-vl.gguf"]

        gen = LlamaSwapConfigGenerator()
        yaml_str = gen.generate_yaml()
        assert "mmproj" in yaml_str


class TestWriteConfig:
    @patch("app.llama_swap_config.get_model_config")
    @patch("app.llama_swap_config.os.path.exists")
    def test_writes_to_file(self, mock_exists, mock_get_config):
        def config_side_effect(module):
            return _mock_config(module, model_path=None)

        mock_get_config.side_effect = config_side_effect
        mock_exists.return_value = True

        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            gen = LlamaSwapConfigGenerator()
            result = gen.write_config(tmp_path)
            assert result is True
            assert os.path.exists(tmp_path)
            with open(tmp_path) as f:
                content = f.read()
            assert "logLevel: info" in content
        finally:
            os.unlink(tmp_path)

    def test_write_failure(self):
        gen = LlamaSwapConfigGenerator()
        result = gen.write_config("/nonexistent/dir/config.yaml")
        assert result is False

    @patch("app.llama_swap_config.get_model_config")
    @patch("app.llama_swap_config.os.path.exists")
    def test_write_unchanged_content_does_not_touch_file(self, mock_exists, mock_get_config):
        """Rewriting identical content must not update mtime — llama-swap's
        -watch-config reloads on every write and kills running model groups."""
        import time as time_module

        def config_side_effect(module):
            return _mock_config(module, model_path=None)

        mock_get_config.side_effect = config_side_effect
        mock_exists.return_value = True

        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            gen = LlamaSwapConfigGenerator()
            assert gen.write_config(tmp_path) is True
            first_mtime = os.path.getmtime(tmp_path)
            time_module.sleep(0.02)
            assert gen.write_config(tmp_path) is True
            assert os.path.getmtime(tmp_path) == first_mtime
        finally:
            os.unlink(tmp_path)

    @patch("app.llama_swap_config.get_model_config")
    @patch("app.llama_swap_config.os.path.exists")
    def test_write_changed_content_rewrites_file(self, mock_exists, mock_get_config):
        """Different content (e.g. model changed in admin) must still be written."""
        import time as time_module

        def config_side_effect(module):
            return _mock_config(module, model_path=None)

        mock_get_config.side_effect = config_side_effect
        mock_exists.return_value = True

        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            gen = LlamaSwapConfigGenerator()
            assert gen.write_config(tmp_path) is True
            first_mtime = os.path.getmtime(tmp_path)
            time_module.sleep(0.02)
            with open(tmp_path) as f:
                first_content = f.read()

            # Change a model parameter -> generated YAML must differ
            def changed_config(module):
                cfg = _mock_config(module, model_path=None)
                if module == "reasoning":
                    cfg["context_length"] = 9999
                return cfg

            mock_get_config.side_effect = changed_config
            assert gen.write_config(tmp_path) is True
            with open(tmp_path) as f:
                assert f.read() != first_content
            assert os.path.getmtime(tmp_path) > first_mtime
        finally:
            os.unlink(tmp_path)


class TestGenerateAndWrite:
    @patch("app.llama_swap_config.LlamaSwapConfigGenerator.write_config", return_value=True)
    def test_generate_and_write(self, mock_write):
        assert generate_and_write() is True
        mock_write.assert_called_once()


class TestMmprojPath:
    @patch("app.llama_swap_config.get_model_config")
    def test_non_multimodal_returns_none(self, mock_get_config):
        gen = LlamaSwapConfigGenerator()
        assert gen.get_mmproj_path("reasoning", "/models/test.gguf") is None

    @patch("app.llama_swap_config.get_model_config")
    @patch("glob.glob")
    def test_multimodal_finds_mmproj(self, mock_glob, mock_get_config):
        mock_glob.return_value = ["/models/qwen-vl/mmproj-qwen-vl.gguf"]
        gen = LlamaSwapConfigGenerator()
        path = gen.get_mmproj_path("multimodal", "/models/qwen-vl/model.gguf")
        assert path == "/models/qwen-vl/mmproj-qwen-vl.gguf"

    @patch("app.llama_swap_config.get_model_config")
    @patch("glob.glob")
    def test_multimodal_no_mmproj(self, mock_glob, mock_get_config):
        mock_glob.return_value = []
        gen = LlamaSwapConfigGenerator()
        assert gen.get_mmproj_path("multimodal", "/models/test.gguf") is None


class TestGetOriginalNgl:
    @patch("app.llama_swap_config.get_model_config")
    @patch("app.utils.get_gguf_models_cached")
    @patch("app.resource_manager.get_resource_manager")
    def test_gguf_suffix_removed_when_looking_up_block_count(self, mock_rm, mock_cache, mock_get_config):
        """Regression: get_gguf_models_cached keys are stored without the .gguf
        suffix, but model_name from DB may include it. Pre-fix the lookup missed
        the entry and fell back to defaults, keeping n_gpu_layers=-1 in the
        degraded (0%) scenario even though the model has known block_count."""
        mock_rm.return_value.compute_llamacpp_config.return_value = {"n_gpu_layers": -1}
        mock_cache.return_value = {"Qwen3VL-8B-Instruct-Q4_K_M": {"block_count": 36}}
        mock_get_config.return_value = {"model_name": "Qwen3VL-8B-Instruct-Q4_K_M.gguf"}
        gen = LlamaSwapConfigGenerator()
        ngl = gen._get_original_ngl("multimodal")
        assert ngl == 36


class TestMetricsEndpoint:
    """llama-server publishes Prometheus metrics on its own backend port."""

    @patch("app.resource_manager.get_resource_manager")
    @patch("app.llama_swap_config.get_model_config")
    def test_command_enables_metrics(self, mock_get_config, mock_get_rm):
        mock_get_config.return_value = {"context_length": 8192}
        mock_rm = MagicMock()
        mock_rm.compute_llamacpp_config.return_value = {"flash_attn": True, "n_gpu_layers": -1}
        mock_get_rm.return_value = mock_rm
        gen = LlamaSwapConfigGenerator()
        cmd = gen.build_cmd("multimodal", "/models/test.gguf")
        assert "--metrics" in cmd, "the monitoring tab needs the Prometheus endpoint"


class TestReasoningBudgetMessage:
    """A spent thinking budget ends with a sentence, not a silent stop."""

    @patch("app.resource_manager.get_resource_manager")
    @patch("app.llama_swap_config.get_model_config")
    def test_budget_message_is_injected(self, mock_get_config, mock_get_rm):
        mock_get_config.return_value = {"context_length": 8192}
        mock_rm = MagicMock()
        mock_rm.compute_llamacpp_config.return_value = {"flash_attn": True, "n_gpu_layers": -1}
        mock_get_rm.return_value = mock_rm
        gen = LlamaSwapConfigGenerator()
        cmd = gen.build_cmd("reasoning", "/models/test.gguf")
        assert "--reasoning-budget" in cmd
        assert "--reasoning-budget-message" in cmd
        assert "Thinking budget reached" in cmd


def _ttl_generator(monkeypatch, *, cpu: bool, ram_mb: int, sizes: dict, overrides: dict | None = None):
    from app import llama_swap_config as mod

    gen = mod.LlamaSwapConfigGenerator()
    monkeypatch.setattr(gen, "_platform_is_cpu", lambda: cpu)
    monkeypatch.setattr(gen, "_total_ram_mb", lambda: ram_mb)
    monkeypatch.setattr(gen, "_model_file_mb", lambda module: sizes.get(module, 0))
    monkeypatch.setattr(
        mod,
        "get_model_config",
        lambda module: {"model_name": f"{module}.gguf", "context_length": 8192, "ttl": (overrides or {}).get(module)},
    )
    return gen


def test_cpu_keeps_the_reasoning_model_resident_when_ram_allows(monkeypatch):
    """On CPU the 10 s TTL means reloading 11 GB from disk for every reasoning answer."""
    gen = _ttl_generator(monkeypatch, cpu=True, ram_mb=32768, sizes={"multimodal": 2500, "reasoning": 11300})

    assert gen.get_ttl("reasoning") == 0


def test_cpu_unloads_reasoning_when_ram_is_tight(monkeypatch):
    gen = _ttl_generator(monkeypatch, cpu=True, ram_mb=16384, sizes={"multimodal": 4700, "reasoning": 11700})

    assert gen.get_ttl("reasoning") == 60


def test_cpu_keeps_the_embedding_model_resident(monkeypatch):
    gen = _ttl_generator(monkeypatch, cpu=True, ram_mb=16384, sizes={"multimodal": 4700, "reasoning": 11700})

    assert gen.get_ttl("embedding") == 0


def test_gpu_ttls_are_unchanged(monkeypatch):
    """On a GPU the reasoning model must yield VRAM to the chat model."""
    gen = _ttl_generator(monkeypatch, cpu=False, ram_mb=32768, sizes={})

    assert gen.get_ttl("reasoning") == 10
    assert gen.get_ttl("embedding") == 1
    assert gen.get_ttl("multimodal") == 0


def test_an_admin_ttl_override_wins_on_any_platform(monkeypatch):
    gen = _ttl_generator(
        monkeypatch, cpu=True, ram_mb=32768, sizes={"multimodal": 2500, "reasoning": 11300},
        overrides={"reasoning": 120},
    )

    assert gen.get_ttl("reasoning") == 120


def test_gpu_group_does_not_swap_behind_the_resource_manager(monkeypatch):
    """On a GPU `ensure_vram_for()` is the arbiter; the group must not evict."""
    from app import llama_swap_config as mod

    gen = mod.LlamaSwapConfigGenerator()
    monkeypatch.setattr(gen, "_platform_is_cpu", lambda: False)
    monkeypatch.setattr(gen, "build_model_entry", lambda module, ngl_override=None: None)

    yaml_str = gen.generate_yaml(include_preload=False)
    block = yaml_str.split("llm_fast:", 1)[1].split("cpu_only:", 1)[0]

    assert "swap: false" in block
    assert "exclusive: false" in block


def test_cpu_group_keeps_llama_swap_in_charge_when_models_do_not_fit(monkeypatch):
    """Without VRAM accounting, one-model-at-a-time is the only RAM guard."""
    from app import llama_swap_config as mod

    gen = mod.LlamaSwapConfigGenerator()
    monkeypatch.setattr(gen, "_platform_is_cpu", lambda: True)
    monkeypatch.setattr(gen, "_cpu_models_coexist", lambda: False)
    monkeypatch.setattr(gen, "build_model_entry", lambda module, ngl_override=None: None)

    yaml_str = gen.generate_yaml(include_preload=False)
    block = yaml_str.split("llm_fast:", 1)[1].split("cpu_only:", 1)[0]

    assert "swap: false" not in block


def test_cpu_group_does_not_swap_when_both_models_fit_in_ram(monkeypatch):
    """With RAM to spare, a reasoning answer must not evict the chat model."""
    from app import llama_swap_config as mod

    gen = mod.LlamaSwapConfigGenerator()
    monkeypatch.setattr(gen, "_platform_is_cpu", lambda: True)
    monkeypatch.setattr(gen, "_cpu_models_coexist", lambda: True)
    monkeypatch.setattr(gen, "build_model_entry", lambda module, ngl_override=None: None)

    yaml_str = gen.generate_yaml(include_preload=False)
    block = yaml_str.split("llm_fast:", 1)[1].split("cpu_only:", 1)[0]

    assert "swap: false" in block


def test_gpu_reasoning_stays_resident_when_the_card_holds_everything(monkeypatch):
    """On a 24 GB card reloading 11.6 GB after every answer is pure loss."""
    gen = _ttl_generator(monkeypatch, cpu=False, ram_mb=32768, sizes={})
    monkeypatch.setattr(gen, "_gpu_models_coexist", lambda: True)

    assert gen.get_ttl("reasoning") == 0


def test_gpu_reasoning_keeps_its_ttl_when_the_card_is_full(monkeypatch):
    gen = _ttl_generator(monkeypatch, cpu=False, ram_mb=32768, sizes={})
    monkeypatch.setattr(gen, "_gpu_models_coexist", lambda: False)

    assert gen.get_ttl("reasoning") == 10
