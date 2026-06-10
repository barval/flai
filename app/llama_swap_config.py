# app/llama_swap_config.py
"""
LlamaSwapConfigGenerator - generates config.yaml for llama-swap from model_configs.

This module reads FLAI model configurations from the database and generates
a YAML configuration file for llama-swap proxy.
"""

import json
import logging
import os
from typing import Any

from app.model_config import get_model_config

logger = logging.getLogger(__name__)

MODELS_DIR = os.getenv("MODELS_DIR", "/models")
CONFIG_DIR = os.getenv("LLAMA_SWAP_CONFIG_DIR", "/config")
CONFIG_FILE = os.getenv("LLAMA_SWAP_CONFIG_FILE", "llama-swap.yaml")

# Progressive degradation steps: fraction of original n_gpu_layers
DEGRADATION_STEPS = [0.75, 0.50, 0.25, 0.0]

DEFAULT_TTL = {
    "chat": 600,
    "embedding": 0,
    "reasoning": 0,
    "multimodal": 0,
}

GROUP_SETTINGS = {
    "chat": {"group": "llm_fast"},
    "embedding": {"group": "llm_fast"},
    "reasoning": {"group": "llm_fast"},
    "multimodal": {"group": "llm_fast"},
}


class LlamaSwapConfigGenerator:
    """Generator for llama-swap configuration from FLAI model_configs."""

    def __init__(self, app=None):
        self.app = app
        self.logger = logging.getLogger(__name__)
        # Per-model degradation tracking: {module: step_index}
        self._degradations: dict[str, int] = {}

    def get_degradation_step(self, module: str) -> int:
        """Get current degradation step index for a module."""
        return self._degradations.get(module, 0)

    def degrade_model(self, module: str) -> int | None:
        """Move to next degradation step for a model. Returns new ngl or None if CPU-only."""
        current = self._degradations.get(module, 0)
        next_step = current + 1
        if next_step >= len(DEGRADATION_STEPS):
            self.logger.warning(f"{module}: already at max degradation, cannot degrade further")
            return None
        self._degradations[module] = next_step
        fraction = DEGRADATION_STEPS[next_step]
        original_ngl = self._get_original_ngl(module)
        if original_ngl is None:
            return None
        new_ngl = 0 if fraction == 0.0 else max(1, int(original_ngl * fraction))
        self.logger.warning(f"{module}: degraded to n_gpu_layers={new_ngl} (step {next_step}, {fraction * 100:.0f}%)")
        return new_ngl

    def reset_degradation(self, module: str):
        """Reset degradation for a model (after successful recovery)."""
        self._degradations.pop(module, None)
        self.logger.info(f"{module}: degradation reset")

    def _get_original_ngl(self, module: str) -> int | None:
        """Get the original (non-degraded) n_gpu_layers for a module.

        Returns the concrete layer count (never -1) so degradation math works correctly.
        """
        try:
            from app.resource_manager import get_resource_manager

            rm = get_resource_manager()
            config = rm.compute_llamacpp_config(module)
            ngl = config.get("n_gpu_layers", -1)
            if ngl == -1:
                from app.utils import get_gguf_models_cached

                mc = get_model_config(module)
                if mc:
                    model_name = mc.get("model_name")
                    if model_name:
                        cache = get_gguf_models_cached("/models")
                        info = cache.get(model_name, {})
                        block_count = info.get("block_count")
                        if block_count:
                            return block_count  # type: ignore[no-any-return]
                defaults = {"chat": 32, "embedding": 12, "reasoning": 48, "multimodal": 32}
                return defaults.get(module, 32)
            return ngl  # type: ignore[no-any-return]
        except Exception:
            return None

    def _get_committed_ngl(self, module: str) -> tuple[int | None, bool]:
        """Get the committed n_gpu_layers for a module, accounting for degradation.

        Returns (ngl, is_degraded).
        """
        step = self._degradations.get(module)
        if step is None or step == 0:
            return None, False
        fraction = DEGRADATION_STEPS[step]
        original = self._get_original_ngl(module)
        if original is None:
            return None, False
        if fraction == 0.0:
            return 0, True
        return max(1, int(original * fraction)), True

    def get_model_path(self, module: str, model_name: str) -> str | None:
        """Get full path to GGUF model file."""
        config = get_model_config(module)
        if not config:
            return None

        # Strip .gguf extension if present in model_name to avoid doubling
        if model_name and model_name.endswith(".gguf"):
            model_name = model_name[:-5]

        model_path = config.get("model_path")
        if model_path:
            if os.path.isabs(model_path):
                return model_path  # type: ignore[no-any-return]
            return os.path.join(MODELS_DIR, model_path)  # type: ignore[no-any-return]

        if model_name:
            import glob

            gguf_in_dir = os.path.join(MODELS_DIR, model_name, model_name + ".gguf")
            if os.path.exists(gguf_in_dir):
                self.logger.info(f"Found model file for {module}: {gguf_in_dir}")
                return gguf_in_dir

            direct_gguf = os.path.join(MODELS_DIR, model_name + ".gguf")
            if os.path.exists(direct_gguf):
                self.logger.info(f"Found model file for {module}: {direct_gguf}")
                return direct_gguf

            if os.path.isdir(os.path.join(MODELS_DIR, model_name)):
                possible_dir = os.path.join(MODELS_DIR, model_name)
                gguf_patterns = [os.path.join(possible_dir, "*.gguf")]
                for gp in gguf_patterns:
                    matches = glob.glob(gp)
                    if matches:
                        self.logger.info(f"Found model file for {module}: {matches[0]}")
                        return matches[0]

            # Fallback: glob search across all subdirectories
            # Handles cases where directory name differs from model_name
            # e.g. model_name="qwen35-4b-instruct-mtp-mxfp4" in dir "Qwen3.5-4B-Instruct-MTP-MXFP4"
            fallback_name = model_name + ".gguf"
            for root, _dirs, files in os.walk(MODELS_DIR):
                if fallback_name in files:
                    found = os.path.join(root, fallback_name)
                    self.logger.info(f"Found model file for {module} (fallback): {found}")
                    return found

            self.logger.warning(f"Model {model_name} not found in {MODELS_DIR}")

        return None

    def get_mmproj_path(self, module: str, model_path: str) -> str | None:
        """Get mmproj path for multimodal models."""
        if not model_path or module != "multimodal":
            return None

        base_dir = os.path.dirname(model_path)
        if not base_dir:
            base_dir = os.path.join(MODELS_DIR, model_path)

        self.logger.info(f"Searching mmproj in base_dir: {base_dir}")

        import glob

        pattern = os.path.join(base_dir, "mmproj*.gguf")
        matches = glob.glob(pattern)
        if matches:
            self.logger.info(f"Found mmproj for {module}: {matches[0]}")
            return matches[0]

        self.logger.warning(f"No mmproj found in {base_dir}")
        return None

    def get_aliases(self, module: str) -> list[str]:
        """Get model aliases from config."""
        config = get_model_config(module)
        if not config:
            return []

        aliases_raw = config.get("aliases")
        if not aliases_raw:
            return []

        try:
            if isinstance(aliases_raw, list):
                return aliases_raw
            return json.loads(aliases_raw)  # type: ignore[no-any-return]
        except (json.JSONDecodeError, TypeError):
            return []

    def get_ttl(self, module: str) -> int:
        """Get TTL for model from config or use default."""
        config = get_model_config(module)
        if not config:
            return DEFAULT_TTL.get(module, 300)

        ttl = config.get("ttl")
        if ttl is not None:
            return ttl  # type: ignore[no-any-return]

        return DEFAULT_TTL.get(module, 300)

    def get_ctx_size(self, module: str) -> int:
        """Get context size for model."""
        config = get_model_config(module)
        if not config:
            return 4096 if module != "reasoning" else 8192
        ctx = config.get("context_length")
        self.logger.info(f"get_ctx_size({module}): config ctx = {repr(ctx)}")
        # Handle None, empty string, 0, or "None" string
        if ctx is None or str(ctx) in ("", "0", "None"):
            # Set sensible defaults per model type
            if module == "embedding":
                result = 2048
            elif module == "reasoning":
                result = 32768
            elif module == "multimodal":
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
            return 4096 if module != "reasoning" else 8192

    def build_model_entry(self, module: str, ngl_override: int | None = None) -> dict[str, Any] | None:
        """Build llama-swap model entry from FLAI model config."""
        config = get_model_config(module)
        if not config:
            self.logger.warning(f"No config for module {module}")
            return None

        model_name = config.get("model_name")
        if not model_name:
            self.logger.warning(f"No model_name for module {module}")
            return None

        model_path = self.get_model_path(module, model_name)
        if not model_path:
            self.logger.warning(f"No model_path for {module} ({model_name})")
            return None

        mmproj = self.get_mmproj_path(module, model_path) if module == "multimodal" else None

        # Use module type as name for proper routing in llama-swap
        # This ensures requests with model=<module_type> are routed correctly
        entry = {
            "cmd": self.build_cmd(module, model_path, mmproj, ngl_override=ngl_override),
            "ttl": self.get_ttl(module),
            "name": module,  # Use module type (embedding, chat, etc.) as name
        }

        # Add aliases for backward compatibility with model_name
        entry["aliases"] = [model_name]

        if module == "chat":
            entry["preload"] = True

        group_info = GROUP_SETTINGS.get(module, {}).get("group")
        if group_info and group_info != "default":
            entry["group"] = group_info

        self.logger.info(f"Built model entry for {module}: {entry}")
        return {module: entry}

    def build_cmd(
        self, module: str, model_path: str, mmproj: str | None = None, ngl_override: int | None = None
    ) -> str:
        """Build llama-server command for model."""
        ctx_size = self.get_ctx_size(module)

        cmd_parts = [
            "llama-server",
            "--port",
            "${PORT}",
            "-m",
            model_path,
            "--host",
            "0.0.0.0",
        ]

        # Add ctx-size only if valid (for non-embedding models)
        # Embedding models can work without ctx-size or with default
        if ctx_size and ctx_size > 0:
            cmd_parts.extend(["--ctx-size", str(ctx_size)])

        if module == "embedding":
            cmd_parts.append("--embeddings")
            # Add batch-size for embedding models to handle larger texts
            # --batch-size is logical, --ubatch-size is physical (default 512)
            # RAG chunks can have up to ~550 tokens, so set both to 2048
            cmd_parts.extend(["--batch-size", "2048", "--ubatch-size", "2048"])

        if module == "reasoning":
            cmd_parts.extend(["--reasoning_format", "none"])

        if mmproj:
            cmd_parts.extend(["--mmproj", mmproj])

        # Apply adaptive GPU config from ResourceManager
        try:
            from app.resource_manager import get_resource_manager

            rm = get_resource_manager()
            config = rm.compute_llamacpp_config(module)

            is_cpu = ngl_override is not None and ngl_override == 0

            if config.get("flash_attn") and not is_cpu:
                cmd_parts.extend(["--flash-attn", "on"])

            # MTP speculative decoding — auto-detected from GGUF metadata
            from app.utils import get_gguf_models_cached
            gguf_cache = get_gguf_models_cached("/models")
            model_key = os.path.basename(model_path).replace(".gguf", "") if model_path else ""
            if gguf_cache.get(model_key, {}).get("supports_mtp"):
                cmd_parts.extend(["--spec-type", "draft-mtp"])

            ngl = ngl_override if ngl_override is not None else config.get("n_gpu_layers", -1)
            if ngl is not None and ngl >= 0:
                cmd_parts.extend(["--n-gpu-layers", str(ngl)])

            if config.get("offload_kqv") and not is_cpu:
                cmd_parts.append("--kv-offload")

            n_cpu_moe = config.get("n_cpu_moe", 0)
            if n_cpu_moe and n_cpu_moe > 0:
                cmd_parts.extend(["--n-cpu-moe", str(n_cpu_moe)])

            if not is_cpu:
                ck = config.get("cache_type_k", "q4_0")
                cv = config.get("cache_type_v", "q4_0")
                cmd_parts.extend(["--cache-type-k", ck, "--cache-type-v", cv])
        except Exception:
            pass

        return " ".join(cmd_parts)

    def generate_yaml(self, ngl_overrides: dict[str, int] | None = None) -> str:
        """Generate full llama-swap YAML configuration.

        Args:
            ngl_overrides: Per-module n_gpu_layers override, e.g. {"reasoning": 10}.
        """
        ngl_overrides = ngl_overrides or {}
        lines = [
            "# Generated by FLAI v8.9",
            "",
            "logLevel: info",
            f"startPort: {os.getenv('LLAMA_SWAP_START_PORT', '10001')}",
            "",
        ]

        groups = {
            "llm_fast": {
                "swap": True,
                "models": ["chat", "embedding", "reasoning", "multimodal"],
            }
        }

        lines.append("groups:")
        for group_name, group_config in groups.items():
            models_list = ", ".join(group_config["models"])  # type: ignore[arg-type]
            if not group_config.get("swap", True):
                lines.append(f"  {group_name}:")
                lines.append("    swap: false")
                lines.append(f"    models: [{models_list}]")
            else:
                lines.append(f"  {group_name}:")
                lines.append(f"    models: [{models_list}]")
        lines.append("")

        lines.append("models:")

        for module in ["chat", "embedding", "reasoning", "multimodal"]:
            ngl_override = ngl_overrides.get(module)
            entry = self.build_model_entry(module, ngl_override=ngl_override)
            if not entry:
                continue

            model_entry = entry[module]
            lines.append(f"  {module}:")
            for key, value in model_entry.items():
                if isinstance(value, dict):
                    for dk, dv in value.items():
                        if isinstance(dv, bool):
                            lines.append(f"    {dk}: {'true' if dv else 'false'}")
                        elif isinstance(dv, str):
                            lines.append(f'    {dk}: "{dv}"')
                        else:
                            lines.append(f"    {dk}: {dv}")
                elif isinstance(value, str):
                    multiline = value.count("\n") > 0
                    if multiline:
                        lines.append(f"    {key}: |")
                        for vline in value.split("\n"):
                            lines.append(f"      {vline}")
                    else:
                        lines.append(f'    {key}: "{value}"')
                elif isinstance(value, bool):
                    lines.append(f"    {key}: {'true' if value else 'false'}")
                else:
                    lines.append(f"    {key}: {value}")

        return "\n".join(lines)

    def write_config(self, path: str | None = None) -> bool:
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
            with open(config_path, "w") as f:
                f.write(yaml_content)
            self.logger.info(f"Wrote llama-swap config to {config_path}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to write config: {e}")
            return False

    def signal_reload(self) -> bool:
        """Signal llama-swap to reload config.

        With -watch-config flag enabled, llama-swap automatically polls for config changes.
        After signaling, polls /running until a model appears (config picked up).
        """
        import time

        import requests

        url = os.getenv("LLAMA_SWAP_URL", "http://flai-llamaswap:8080").rstrip("/")

        try:
            response = requests.post(f"{url}/reload", timeout=10)
            if response.status_code not in (200, 404):
                self.logger.warning(f"llama-swap reload returned {response.status_code}")
                return False
        except requests.exceptions.RequestException as e:
            self.logger.warning(f"Could not signal llama-swap reload: {e}")
            return False

        self.logger.info("llama-swap reload signaled, waiting for config pick-up...")

        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                r = requests.get(f"{url}/running", timeout=2)
                if r.status_code == 200:
                    running = r.json().get("running", [])
                    if running:
                        self.logger.info(f"llama-swap reloaded: {len(running)} model(s) running")
                        return True
            except Exception:
                pass
            time.sleep(0.5)

        self.logger.warning("llama-swap did not reload config within 5s")
        return True  # config was written, reload will happen eventually

    def degrade_and_reload(self, module: str) -> bool:
        """Degrade one model's GPU usage and reload llama-swap config.

        Falls back to CPU-only (ngl=0) for the degraded model when max degradation reached.
        Returns True if degradation was applied and reload signaled.
        """
        ngl = self.degrade_model(module)
        if ngl is None:
            if self._degradations.get(module, 0) >= len(DEGRADATION_STEPS) - 1:
                self.logger.warning(f"{module}: degraded to CPU-only (ngl=0)")
                ngl = 0
            else:
                return False

        overrides: dict[str, int] = {}
        for mod, step in self._degradations.items():
            frac = DEGRADATION_STEPS[step]
            orig = self._get_original_ngl(mod)
            if orig is not None:
                if frac == 0.0:
                    overrides[mod] = 0
                else:
                    overrides[mod] = max(1, int(orig * frac))
        overrides[module] = ngl

        new_yaml = self.generate_yaml(ngl_overrides=overrides)
        config_path = os.path.join(CONFIG_DIR, CONFIG_FILE)
        try:
            with open(config_path, "w") as f:
                f.write(new_yaml)
            self.logger.info(f"Wrote degraded config to {config_path} (module={module}, ngl={ngl})")
        except Exception as e:
            self.logger.error(f"Failed to write degraded config: {e}")
            return False

        self.signal_reload()
        return True


def generate_and_write(app=None) -> bool:
    """Generate GPU config and write it."""
    generator = LlamaSwapConfigGenerator(app)
    return generator.write_config()
