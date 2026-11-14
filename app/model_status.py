"""Whether a model role is loaded right now, straight from llama-swap.

Statuses in the chat stream and in the task view announce a model load. That
announcement was always emitted before the call, while llama-swap decides for
itself whether anything has to be loaded: in single-model mode the one universal
entry is resident (ttl 0), and in two-model mode on CPU both can stay resident
too. The honest answer comes from ``/running``, and it is the same answer on
GPU and CPU, because both modes are served by llama-swap.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def model_is_ready(module: str) -> bool:
    """True when ``module`` is served by a model llama-swap reports as ready.

    ``module`` is the role name (``multimodal``, ``reasoning``): in single-model
    mode both map to the one universal entry, so asking for either answers
    "ready" while that entry is loaded.
    """
    try:
        import requests

        from app.config import setting
        from app.model_config import get_model_config
        from app.universal_mode import entry_name

        url = str(setting("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")).rstrip("/")
        response = requests.get(f"{url}/running", timeout=3)
        if response.status_code != 200:
            return False

        config = get_model_config(module) or {}
        model_name = str(config.get("model_name") or "")
        entry = entry_name(module)

        for running in response.json().get("running", []):
            if running.get("state") != "ready":
                # "starting" is a load in progress: the caller must say so.
                continue
            if running.get("model") == entry or (model_name and model_name in running.get("cmd", "")):
                return True
        return False
    except Exception as exc:  # noqa: BLE001 — an unreachable backend is "not ready"
        logger.debug("model_is_ready[%s]: %s", module, exc)
        return False


def universal_mode_active() -> bool:
    """True when one model serves every role (the status wording differs)."""
    try:
        from app.universal_mode import is_enabled

        return is_enabled()
    except Exception:
        return False
