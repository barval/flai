# app/llama_swap_config.py
"""
LlamaSwapConfigGenerator - generates config.yaml for llama-swap from model_configs.

This module reads FLAI model configurations from the database and generates
a YAML configuration file for llama-swap proxy.
"""

import logging
import json
import os
from typing import Dict, List, Optional, Any

from app.model_config import get_model_config

logger = logging.getLogger(__name__)

MODELS_DIR = os.getenv('MODELS_DIR', '/models')
CONFIG_DIR = os.getenv('LLAMA_SWAP_CONFIG_DIR', '/config')
CONFIG_FILE = os.getenv('LLAMA_SWAP_CONFIG_FILE', 'llama-swap.yaml')

DEFAULT_TTL = {
    'chat': 600,
    'embedding': 180,
    'reasoning': 900,
    'multimodal': 600,
}

GROUP_SETTINGS = {
    'chat': {'group': 'llm_fast'},
    'embedding': {'group': 'llm_fast'},
    'reasoning': {'group': 'default'},
    'multimodal': {'group': 'default'},
}


class LlamaSwapConfigGenerator:
    """Generator for llama-swap configuration from FLAI model_configs."""

    def __init__(self, app=None):
        self.app = app
        self.logger = logging.getLogger(__name__)

    def get_model_path(self, module: str, model_name: str) -> Optional[str]:
        """Get full path to GGUF model file."""
        config = get_model_config(module)
        if not config:
            return None

        # Strip .gguf extension if present in model_name to avoid doubling
        if model_name and model_name.endswith('.gguf'):
            model_name = model_name[:-5]

        model_path = config.get('model_path')
        if model_path:
            if os.path.isabs(model_path):
                return model_path
            return os.path.join(MODELS_DIR, model_path)

        if model_name:
            import glob
            
            gguf_in_dir = os.path.join(MODELS_DIR, model_name, model_name + '.gguf')
            if os.path.exists(gguf_in_dir):
                self.logger.info(f"Found model file for {module}: {gguf_in_dir}")
                return gguf_in_dir
            
            direct_gguf = os.path.join(MODELS_DIR, model_name + '.gguf')
            if os.path.exists(direct_gguf):
                self.logger.info(f"Found model file for {module}: {direct_gguf}")
                return direct_gguf
            
            if os.path.isdir(os.path.join(MODELS_DIR, model_name)):
                possible_dir = os.path.join(MODELS_DIR, model_name)
                gguf_patterns = [os.path.join(possible_dir, '*.gguf')]
                for gp in gguf_patterns:
                    matches = glob.glob(gp)
                    if matches:
                        self.logger.info(f"Found model file for {module}: {matches[0]}")
                        return matches[0]
            
            self.logger.warning(f"Model {model_name} not found in {MODELS_DIR}")

        return None

    def get_mmproj_path(self, module: str, model_path: str) -> Optional[str]:
        """Get mmproj path for multimodal models."""
        if not model_path or module != 'multimodal':
            return None

        base_dir = os.path.dirname(model_path)
        if not base_dir:
            base_dir = os.path.join(MODELS_DIR, model_path)

        self.logger.info(f"Searching mmproj in base_dir: {base_dir}")

        import glob
        pattern = os.path.join(base_dir, 'mmproj*.gguf')
        matches = glob.glob(pattern)
        if matches:
            self.logger.info(f"Found mmproj for {module}: {matches[0]}")
            return matches[0]

        self.logger.warning(f"No mmproj found in {base_dir}")
        return None

    def get_aliases(self, module: str) -> List[str]:
        """Get model aliases from config."""
        config = get_model_config(module)
        if not config:
            return []

        aliases_raw = config.get('aliases')
        if not aliases_raw:
            return []

        try:
            if isinstance(aliases_raw, list):
                return aliases_raw
            return json.loads(aliases_raw)
        except (json.JSONDecodeError, TypeError):
            return []

    def get_ttl(self, module: str) -> int:
        """Get TTL for model from config or use default."""
        config = get_model_config(module)
        if not config:
            return DEFAULT_TTL.get(module, 300)

        ttl = config.get('ttl')
        if ttl is not None:
            return ttl

        return DEFAULT_TTL.get(module, 300)

    def get_ctx_size(self, module: str) -> int:
        """Get context size for model."""
        config = get_model_config(module)
        if not config:
            return 4096 if module != 'reasoning' else 8192
        ctx = config.get('context_length')
        self.logger.info(f"get_ctx_size({module}): config ctx = {repr(ctx)}")
        # Handle None, empty string, 0, or "None" string
        if ctx is None or str(ctx) in ('', '0', 'None'):
            # Set sensible defaults per model type
            if module == 'embedding':
                result = 2048
            elif module == 'reasoning':
                result = 32768
            elif module == 'multimodal':
                result = 8192
            else:
                result = 4096
            self.logger.info(f"get_ctx_size({module}): using default {result}")
            return result
        try:
            result = int(ctx)
            self.logger.info(f"get_ctx_size({module}): using DB value {result}")
            return result
        except (ValueError, TypeError):
            self.logger.warning(f"get_ctx_size({module}): invalid ctx {repr(ctx)}, using default")
            return 4096 if module != 'reasoning' else 8192

    def build_model_entry(self, module: str) -> Optional[Dict[str, Any]]:
        """Build llama-swap model entry from FLAI model config."""
        config = get_model_config(module)
        if not config:
            self.logger.warning(f"No config for module {module}")
            return None

        model_name = config.get('model_name')
        if not model_name:
            self.logger.warning(f"No model_name for module {module}")
            return None

        model_path = self.get_model_path(module, model_name)
        if not model_path:
            self.logger.warning(f"No model_path for {module} ({model_name})")
            return None

        mmproj = self.get_mmproj_path(module, model_path) if module == 'multimodal' else None

        # Use module type as name for proper routing in llama-swap
        # This ensures requests with model=<module_type> are routed correctly
        entry = {
            'cmd': self.build_cmd(module, model_path, mmproj),
            'ttl': self.get_ttl(module),
            'name': module,  # Use module type (embedding, chat, etc.) as name
        }

        # Add aliases for backward compatibility with model_name
        entry['aliases'] = [model_name]

        if module == 'chat':
            entry['preload'] = True

        group_info = GROUP_SETTINGS.get(module, {}).get('group')
        if group_info and group_info != 'default':
            entry['group'] = group_info

        self.logger.info(f"Built model entry for {module}: {entry}")
        return {module: entry}

    def build_cmd(self, module: str, model_path: str, mmproj: Optional[str] = None) -> str:
        """Build llama-server command for model."""
        ctx_size = self.get_ctx_size(module)

        cmd_parts = [
            'llama-server',
            '--port', '${PORT}',
            '-m', model_path,
            '--host', '0.0.0.0',
        ]

        # Add ctx-size only if valid (for non-embedding models)
        # Embedding models can work without ctx-size or with default
        if ctx_size and ctx_size > 0:
            cmd_parts.extend(['--ctx-size', str(ctx_size)])

        if module == 'embedding':
            cmd_parts.append('--embeddings')
            # Add batch-size for embedding models to handle larger texts
            # --batch-size is logical, --ubatch-size is physical (default 512)
            # RAG chunks can have up to ~550 tokens, so set both to 2048
            cmd_parts.extend(['--batch-size', '2048', '--ubatch-size', '2048'])

        if mmproj:
            cmd_parts.extend(['--mmproj', mmproj])

        return ' '.join(cmd_parts)

    def generate_yaml(self) -> str:
        """Generate full llama-swap YAML configuration."""
        lines = [
            '# Generated by FLAI v8.1',
            '',
            'logLevel: info',
            f'startPort: {os.getenv("LLAMA_SWAP_START_PORT", "10001")}',
            '',
        ]

        groups = {
            'llm_fast': {
                'swap': False,
                'models': ['chat', 'embedding'],
            }
        }

        lines.append('groups:')
        for group_name, group_config in groups.items():
            models_list = ', '.join(group_config['models'])
            if not group_config.get('swap', True):
                lines.append(f'  {group_name}:')
                lines.append(f'    swap: false')
                lines.append(f'    models: [{models_list}]')
            else:
                lines.append(f'  {group_name}:')
                lines.append(f'    models: [{models_list}]')
        lines.append('')

        lines.append('models:')

        for module in ['chat', 'embedding', 'reasoning', 'multimodal']:
            entry = self.build_model_entry(module)
            if not entry:
                continue

            model_entry = entry[module]
            lines.append(f'  {module}:')
            for key, value in model_entry.items():
                if isinstance(value, dict):
                    for dk, dv in value.items():
                        if isinstance(dv, bool):
                            lines.append(f'    {dk}: {"true" if dv else "false"}')
                        elif isinstance(dv, str):
                            lines.append(f'    {dk}: "{dv}"')
                        else:
                            lines.append(f'    {dk}: {dv}')
                elif isinstance(value, str):
                    multiline = value.count('\n') > 0
                    if multiline:
                        lines.append(f'    {key}: |')
                        for vline in value.split('\n'):
                            lines.append(f'      {vline}')
                    else:
                        lines.append(f'    {key}: "{value}"')
                elif isinstance(value, bool):
                    lines.append(f'    {key}: {"true" if value else "false"}')
                else:
                    lines.append(f'    {key}: {value}')

        return '\n'.join(lines)

    def write_config(self, path: Optional[str] = None) -> bool:
        """Write config to file."""
        config_path = path or os.path.join(CONFIG_DIR, CONFIG_FILE)

        config_dir = os.path.dirname(config_path)
        if config_dir and not os.path.exists(config_dir):
            try:
                os.makedirs(config_dir, exist_ok=True)
                self.logger.info(f"Created config directory: {config_dir}")
            except Exception as e:
                self.logger.error(f"Failed to create config directory: {e}")
                return False

        yaml_content = self.generate_yaml()

        try:
            with open(config_path, 'w') as f:
                f.write(yaml_content)
            self.logger.info(f"Wrote llama-swap config to {config_path}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to write config: {e}")
            return False

    def signal_reload(self) -> bool:
        """Signal llama-swap to reload config.
        
        With -watch-config flag enabled, llama-swap automatically polls for config changes.
        This method is kept for manual reload trigger if needed.
        """
        import requests

        url = os.getenv('LLAMA_SWAP_URL', 'http://flai-llamaswap:8080')

        try:
            response = requests.post(f"{url.rstrip('/')}/reload", timeout=10)
            if response.status_code in (200, 404):
                self.logger.info("llama-swap reload signaled")
                return True
            self.logger.warning(f"llama-swap reload returned {response.status_code}")
            return False
        except requests.exceptions.RequestException as e:
            self.logger.warning(f"Could not signal llama-swap reload: {e}")
            return False
            self.logger.warning(f"Failed to signal reload: {e}")

        return False


def generate_and_write(app=None) -> bool:
    """Convenience function to generate and write config."""
    generator = LlamaSwapConfigGenerator(app)
    return generator.write_config()