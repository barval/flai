"""Tests for llama-swap configuration generator."""
import json
import os
import tempfile
from unittest.mock import patch

from app.llama_swap_config import (
    DEFAULT_TTL,
    LlamaSwapConfigGenerator,
    generate_and_write,
)


def _mock_config(module, **overrides):
    configs = {
        'chat': {
            'module': 'chat',
            'model_name': 'Qwen3-4B-Instruct-2507-Q4_K_M',
            'context_length': 8192,
            'temperature': 0.1,
            'timeout': 120,
            'service_url': 'http://flai-llamacpp:8033',
            'ttl': None,
            'model_path': None,
            'aliases': None,
        },
        'embedding': {
            'module': 'embedding',
            'model_name': 'bge-m3-Q8_0',
            'context_length': 512,
            'timeout': 120,
            'service_url': 'http://flai-llamacpp:8033',
            'ttl': None,
            'model_path': None,
            'aliases': None,
        },
        'reasoning': {
            'module': 'reasoning',
            'model_name': 'gpt-oss-20b-Q4_K_M',
            'context_length': 8192,
            'temperature': 0.7,
            'timeout': 120,
            'service_url': 'http://flai-llamacpp:8033',
            'ttl': None,
            'model_path': None,
            'aliases': None,
        },
        'multimodal': {
            'module': 'multimodal',
            'model_name': 'Qwen3VL-8B-Instruct-Q4_K_M',
            'context_length': 8192,
            'temperature': 0.7,
            'timeout': 120,
            'service_url': 'http://flai-llamacpp:8033',
            'ttl': None,
            'model_path': None,
            'aliases': None,
        },
    }
    base = configs.get(module, {})
    base.update(overrides)
    return base


class TestGetModelPath:
    def setup_method(self):
        self.temp_dir = tempfile.mkdtemp()

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch('app.llama_swap_config.get_model_config')
    def test_no_config(self, mock_get_config):
        mock_get_config.return_value = None
        gen = LlamaSwapConfigGenerator()
        path = gen.get_model_path('chat', 'test-model')
        assert path is None

    @patch('app.llama_swap_config.get_model_config')
    def test_model_path_in_config(self, mock_get_config):
        mock_get_config.return_value = _mock_config('chat', model_path='/models/custom.gguf')
        gen = LlamaSwapConfigGenerator()
        path = gen.get_model_path('chat', 'Qwen3-4B-Instruct-2507-Q4_K_M')
        assert path == '/models/custom.gguf'

    @patch('app.llama_swap_config.get_model_config')
    def test_model_path_relative(self, mock_get_config):
        mock_get_config.return_value = _mock_config('chat', model_path='subdir/model.gguf')
        gen = LlamaSwapConfigGenerator()
        path = gen.get_model_path('chat', 'Qwen3-4B-Instruct-2507-Q4_K_M')
        assert '/models/subdir/model.gguf' in path

    @patch('app.llama_swap_config.get_model_config')
    @patch('app.llama_swap_config.os.path.exists')
    def test_direct_gguf(self, mock_exists, mock_get_config):
        mock_get_config.return_value = _mock_config('chat', model_path=None)
        def exists_side_effect(p):
            return p == '/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf'
        mock_exists.side_effect = exists_side_effect

        gen = LlamaSwapConfigGenerator()
        path = gen.get_model_path('chat', 'Qwen3-4B-Instruct-2507-Q4_K_M')
        assert path == '/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf'

    @patch('app.llama_swap_config.get_model_config')
    @patch('app.llama_swap_config.os.path.exists')
    def test_gguf_extension_stripping(self, mock_exists, mock_get_config):
        mock_get_config.return_value = _mock_config('chat', model_path=None)
        def exists_side_effect(p):
            return p == '/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf'
        mock_exists.side_effect = exists_side_effect

        gen = LlamaSwapConfigGenerator()
        path = gen.get_model_path('chat', 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf')
        assert path == '/models/Qwen3-4B-Instruct-2507-Q4_K_M.gguf'


class TestGetTtl:
    @patch('app.llama_swap_config.get_model_config')
    def test_default_ttl(self, mock_get_config):
        mock_get_config.return_value = None
        gen = LlamaSwapConfigGenerator()
        assert gen.get_ttl('chat') == DEFAULT_TTL['chat']
        assert gen.get_ttl('unknown') == 300

    @patch('app.llama_swap_config.get_model_config')
    def test_custom_ttl(self, mock_get_config):
        mock_get_config.return_value = {'ttl': 999}
        gen = LlamaSwapConfigGenerator()
        assert gen.get_ttl('chat') == 999


class TestGetAliases:
    @patch('app.llama_swap_config.get_model_config')
    def test_no_aliases(self, mock_get_config):
        mock_get_config.return_value = {}
        gen = LlamaSwapConfigGenerator()
        assert gen.get_aliases('chat') == []

    @patch('app.llama_swap_config.get_model_config')
    def test_aliases_json_string(self, mock_get_config):
        mock_get_config.return_value = {'aliases': json.dumps(['model-a', 'model-b'])}
        gen = LlamaSwapConfigGenerator()
        assert gen.get_aliases('chat') == ['model-a', 'model-b']

    @patch('app.llama_swap_config.get_model_config')
    def test_aliases_list(self, mock_get_config):
        mock_get_config.return_value = {'aliases': ['a', 'b']}
        gen = LlamaSwapConfigGenerator()
        assert gen.get_aliases('chat') == ['a', 'b']


class TestGetCtxSize:
    @patch('app.llama_swap_config.get_model_config')
    def test_no_config(self, mock_get_config):
        mock_get_config.return_value = None
        gen = LlamaSwapConfigGenerator()
        assert gen.get_ctx_size('chat') == 4096
        assert gen.get_ctx_size('reasoning') == 8192

    @patch('app.llama_swap_config.get_model_config')
    def test_config_ctx(self, mock_get_config):
        mock_get_config.return_value = {'context_length': 16384}
        gen = LlamaSwapConfigGenerator()
        assert gen.get_ctx_size('chat') == 16384

    @patch('app.llama_swap_config.get_model_config')
    def test_none_ctx_uses_default(self, mock_get_config):
        mock_get_config.return_value = {'context_length': None}
        gen = LlamaSwapConfigGenerator()
        assert gen.get_ctx_size('embedding') == 2048
        assert gen.get_ctx_size('chat') == 4096


class TestBuildModelEntry:
    @patch('app.llama_swap_config.get_model_config')
    def test_no_config(self, mock_get_config):
        mock_get_config.return_value = None
        gen = LlamaSwapConfigGenerator()
        assert gen.build_model_entry('chat') is None

    @patch('app.llama_swap_config.get_model_config')
    def test_no_model_name(self, mock_get_config):
        mock_get_config.return_value = {'model_name': ''}
        gen = LlamaSwapConfigGenerator()
        assert gen.build_model_entry('chat') is None

    @patch('app.llama_swap_config.get_model_config')
    @patch('app.llama_swap_config.os.path.exists')
    def test_full_entry(self, mock_exists, mock_get_config):
        def config_side_effect(module):
            return _mock_config(module, model_path=None)

        mock_get_config.side_effect = config_side_effect
        mock_exists.return_value = True

        gen = LlamaSwapConfigGenerator()
        entry = gen.build_model_entry('chat')
        assert entry is not None
        assert 'chat' in entry
        assert 'cmd' in entry['chat']
        assert 'ttl' in entry['chat']
        assert 'aliases' in entry['chat']
        assert entry['chat'].get('preload') is True

    @patch('app.llama_swap_config.get_model_config')
    @patch('app.llama_swap_config.os.path.exists')
    def test_entry_with_group(self, mock_exists, mock_get_config):
        def config_side_effect(module):
            return _mock_config(module)

        mock_get_config.side_effect = config_side_effect
        mock_exists.return_value = True

        gen = LlamaSwapConfigGenerator()
        entry = gen.build_model_entry('chat')
        assert entry['chat'].get('group') == 'llm_fast'
        assert entry['chat'].get('preload') is True

    @patch('app.llama_swap_config.get_model_config')
    @patch('app.llama_swap_config.os.path.exists')
    def test_no_group_for_default(self, mock_exists, mock_get_config):
        def config_side_effect(module):
            return _mock_config(module)

        mock_get_config.side_effect = config_side_effect
        mock_exists.return_value = True

        gen = LlamaSwapConfigGenerator()
        entry = gen.build_model_entry('multimodal')
        assert entry['multimodal'].get('group') is None


class TestBuildCmd:
    @patch('app.llama_swap_config.get_model_config')
    def test_basic_cmd(self, mock_get_config):
        mock_get_config.return_value = {'context_length': 8192}
        gen = LlamaSwapConfigGenerator()
        cmd = gen.build_cmd('chat', '/models/test.gguf')
        assert cmd.startswith('llama-server')
        assert '--port ${PORT}' in cmd
        assert '-m /models/test.gguf' in cmd
        assert '--ctx-size 8192' in cmd

    @patch('app.llama_swap_config.get_model_config')
    def test_embedding_cmd(self, mock_get_config):
        mock_get_config.return_value = {'context_length': 512}
        gen = LlamaSwapConfigGenerator()
        cmd = gen.build_cmd('embedding', '/models/bge.gguf')
        assert '--embeddings' in cmd
        assert '--batch-size 2048' in cmd
        assert '--ubatch-size 2048' in cmd

    @patch('app.llama_swap_config.get_model_config')
    def test_multimodal_cmd(self, mock_get_config):
        mock_get_config.return_value = {'context_length': 8192}
        gen = LlamaSwapConfigGenerator()
        cmd = gen.build_cmd('multimodal', '/models/vl.gguf', mmproj='/models/mmproj.gguf')
        assert '--mmproj /models/mmproj.gguf' in cmd

    @patch('app.llama_swap_config.get_model_config')
    def test_zero_ctx_size_omitted(self, mock_get_config):
        mock_get_config.return_value = {'context_length': None}
        gen = LlamaSwapConfigGenerator()
        cmd = gen.build_cmd('chat', '/models/test.gguf')
        assert '--ctx-size 4096' in cmd


class TestGenerateYaml:
    @patch('app.llama_swap_config.get_model_config')
    @patch('app.llama_swap_config.os.path.exists')
    def test_generates_valid_yaml(self, mock_exists, mock_get_config):
        def config_side_effect(module):
            return _mock_config(module, model_path=None)

        mock_get_config.side_effect = config_side_effect
        mock_exists.return_value = True

        gen = LlamaSwapConfigGenerator()
        yaml_str = gen.generate_yaml()

        assert 'logLevel: info' in yaml_str
        assert 'startPort: 10001' in yaml_str
        assert 'groups:' in yaml_str
        assert 'llm_fast:' in yaml_str
        assert 'swap: false' in yaml_str
        assert 'models:' in yaml_str
        assert 'chat:' in yaml_str
        assert 'embedding:' in yaml_str
        assert 'reasoning:' in yaml_str
        assert 'multimodal:' in yaml_str
        assert 'preload: true' in yaml_str
        assert 'llama-server' in yaml_str

    @patch('app.llama_swap_config.get_model_config')
    @patch('app.llama_swap_config.os.path.exists')
    def test_missing_config_skips_module(self, mock_exists, mock_get_config):
        def config_side_effect(m):
            return _mock_config(m, model_path=None) if m == 'chat' else None
        mock_get_config.side_effect = config_side_effect
        mock_exists.return_value = True

        gen = LlamaSwapConfigGenerator()
        yaml_str = gen.generate_yaml()
        assert 'chat:' in yaml_str
        assert 'embedding:' not in yaml_str
        assert 'reasoning:' not in yaml_str

    @patch('app.llama_swap_config.get_model_config')
    @patch('app.llama_swap_config.os.path.exists')
    @patch('glob.glob')
    def test_multimodal_includes_mmproj(self, mock_glob, mock_exists, mock_get_config):
        def config_side_effect(module):
            return _mock_config(module, model_path=None)

        mock_get_config.side_effect = config_side_effect
        mock_exists.return_value = True
        mock_glob.return_value = ['/models/Qwen3VL-8B-Instruct-Q4_K_M/mmproj-qwen-vl.gguf']

        gen = LlamaSwapConfigGenerator()
        yaml_str = gen.generate_yaml()
        assert 'mmproj' in yaml_str


class TestWriteConfig:
    @patch('app.llama_swap_config.get_model_config')
    @patch('app.llama_swap_config.os.path.exists')
    def test_writes_to_file(self, mock_exists, mock_get_config):
        def config_side_effect(module):
            return _mock_config(module, model_path=None)

        mock_get_config.side_effect = config_side_effect
        mock_exists.return_value = True

        with tempfile.NamedTemporaryFile(suffix='.yaml', delete=False) as tmp:
            tmp_path = tmp.name

        try:
            gen = LlamaSwapConfigGenerator()
            result = gen.write_config(tmp_path)
            assert result is True
            assert os.path.exists(tmp_path)
            with open(tmp_path) as f:
                content = f.read()
            assert 'logLevel: info' in content
        finally:
            os.unlink(tmp_path)

    def test_write_failure(self):
        gen = LlamaSwapConfigGenerator()
        result = gen.write_config('/nonexistent/dir/config.yaml')
        assert result is False


class TestGenerateAndWrite:
    @patch('app.llama_swap_config.LlamaSwapConfigGenerator.write_config', return_value=True)
    def test_generate_and_write(self, mock_write):
        assert generate_and_write() is True
        mock_write.assert_called_once()


class TestMmprojPath:
    @patch('app.llama_swap_config.get_model_config')
    def test_non_multimodal_returns_none(self, mock_get_config):
        gen = LlamaSwapConfigGenerator()
        assert gen.get_mmproj_path('chat', '/models/test.gguf') is None

    @patch('app.llama_swap_config.get_model_config')
    @patch('glob.glob')
    def test_multimodal_finds_mmproj(self, mock_glob, mock_get_config):
        mock_glob.return_value = ['/models/qwen-vl/mmproj-qwen-vl.gguf']
        gen = LlamaSwapConfigGenerator()
        path = gen.get_mmproj_path('multimodal', '/models/qwen-vl/model.gguf')
        assert path == '/models/qwen-vl/mmproj-qwen-vl.gguf'

    @patch('app.llama_swap_config.get_model_config')
    @patch('glob.glob')
    def test_multimodal_no_mmproj(self, mock_glob, mock_get_config):
        mock_glob.return_value = []
        gen = LlamaSwapConfigGenerator()
        assert gen.get_mmproj_path('multimodal', '/models/test.gguf') is None
