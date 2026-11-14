"""Load the chat model before the first message, through the VRAM gate.

The first chat after a restart used to be slow until llama-swap had swapped the
multimodal model in. The answer was a llama-swap `on_startup.preload` hook, and
it was the wrong tool: llama-swap started the model outside ``ensure_vram_for()``,
next to whatever was already resident, and when the card could not hold it
llama.cpp kept the weights in RAM — measured, 11 tok/s instead of 75 for eight
hours, with nothing in the log.

The warm-up therefore lives here: a background thread that waits for llama-swap,
asks the gate for room (which evicts residents properly), and only then asks for
the model. It is the same request path a user message takes, so it cannot produce
a placement no user message could produce.
"""

from __future__ import annotations

import logging
import threading
import time

import requests
from flask import Flask

from app.config import setting

logger = logging.getLogger(__name__)

# Give llama-swap time to open its socket after a compose restart.
LLAMASWAP_READY_TIMEOUT_S = 90
# The model itself is 5-8 GB; a cold load takes seconds, not a minute.
WARMUP_TIMEOUT_S = 120


def _llama_swap_url() -> str:
    # Through app.config, so a URL saved in the admin panel reaches the warm-up:
    # reading the environment directly would ignore the panel (the setting
    # coverage test guards exactly that).
    return str(setting("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")).rstrip("/")


def wait_for_llama_swap(timeout: float = LLAMASWAP_READY_TIMEOUT_S) -> bool:
    """Block until llama-swap answers ``/running`` or the deadline passes."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(f"{_llama_swap_url()}/running", timeout=2).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def load_chat_model(timeout: int = WARMUP_TIMEOUT_S) -> bool:
    """Ask llama-swap for the multimodal model and wait until it is running.

    A one-token completion is what makes llama-swap start the server; polling
    ``/running`` confirms it, so a caller can tell "loaded" from "load failed"
    instead of reporting success for a request that only warmed the queue.
    """
    import requests

    from app.model_config import get_model_config

    url = _llama_swap_url()
    # In single-model mode there is no "multimodal" entry: asking for it answered
    # 404 and the model was never warmed, so the first message after a restart
    # paid the full 14 GB load.
    from app.universal_mode import entry_name

    try:
        response = requests.post(
            f"{url}/v1/chat/completions",
            json={
                "model": entry_name("multimodal"),
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            },
            timeout=timeout,
        )
        if response.status_code != 200:
            logger.warning(f"chat model warm-up returned HTTP {response.status_code}")
            return False
    except Exception as e:
        logger.warning(f"chat model warm-up request failed: {e}")
        return False

    config = get_model_config("multimodal")
    model_name = config.get("model_name", "") if config else ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            check = requests.get(f"{url}/running", timeout=2)
            if check.status_code == 200:
                running = check.json().get("running", [])
                if model_name and any(model_name in entry.get("cmd", "") for entry in running):
                    return True
        except Exception:
            pass
        time.sleep(1)
    logger.warning("chat model warm-up timed out before the model reached the running state")
    return False


def warm_up_chat_model(app: Flask, timeout: int = WARMUP_TIMEOUT_S) -> bool:
    """Make the multimodal model resident, going through the VRAM gate first."""
    from app.resource_manager import get_resource_manager

    manager = get_resource_manager()
    if not manager.ensure_vram_for("multimodal"):
        logger.warning("chat model warm-up skipped: the VRAM gate refused room for it")
        return False
    return load_chat_model(timeout=timeout)


def schedule_chat_model_warmup(app: Flask, delay: float = 0.0) -> threading.Thread | None:
    """Start the warm-up in a daemon thread; return it (None when not applicable).

    Only a server process warms anything: a CLI process must not load a model,
    and two web workers would race for the same load.
    """

    def _run() -> None:
        if delay:
            time.sleep(delay)
        with app.app_context():
            try:
                if not wait_for_llama_swap():
                    app.logger.warning("chat model warm-up skipped: llama-swap did not answer in time")
                    return
                started = time.time()
                if warm_up_chat_model(app):
                    app.logger.info(f"Chat model warm and resident ({time.time() - started:.1f}s)")
            except Exception as e:
                app.logger.warning(f"Chat model warm-up failed: {e}")

    thread = threading.Thread(target=_run, name="chat-model-warmup", daemon=True)
    thread.start()
    return thread
