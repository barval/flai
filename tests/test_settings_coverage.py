"""Every parameter in the panel is read from the app config, or is bootstrap.

Two rules, both enforced here:

* a key the admin panel can change must be read through ``app.config`` (or
  ``app.config.setting()``), otherwise saving it changes nothing;
* a key that *is* read with ``os.getenv`` must be declared bootstrap, so the
  reason it is not in the panel is written down rather than forgotten.

The allowlist below is a ratchet: it lists the call sites that still read a
panel key from the environment directly. It may shrink; adding an entry to make
a new failure pass is the thing this test exists to prevent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.settings_registry import BOOTSTRAP_KEYS, movable_keys

APP_ROOT = Path(__file__).resolve().parent.parent
_READ_RE = re.compile(r'os\.(?:getenv|environ\.get)\(\s*"([A-Z][A-Z0-9_]+)"')

# (file, key) pairs that still take a panel-able key from the environment.
# Mostly the llama-swap URL, which several GPU-path helpers read without an app
# context; converting them is a separate change because each call site has to
# decide what to do when there is no app.
KNOWN_DIRECT_READS: set[tuple[str, str]] = {
    ("app/cli.py", "LLAMA_SWAP_URL"),
    ("app/llamacpp_client.py", "LLAMACP_BACKEND"),
    ("app/llamacpp_client.py", "LLAMA_SWAP_URL"),
    ("app/llama_swap_config.py", "LLAMA_SWAP_URL"),
    ("app/model_hub.py", "HUGGINGFACE_TOKEN"),
    ("app/model_hub.py", "MODEL_HUB_FREE_MARGIN_GB"),
    ("app/model_hub.py", "MODEL_HUB_MAX_FILE_GB"),
    ("app/model_hub.py", "MODEL_HUB_TIMEOUT_S"),
    ("app/queue.py", "LLAMA_SWAP_URL"),
    ("app/resource_manager.py", "LLAMACP_BACKEND"),
    ("app/resource_manager.py", "LLAMA_SWAP_URL"),
    ("app/resource_manager.py", "LTX_VIDEO_WRAPPER_URL"),
    ("app/resource_manager.py", "VIDEO_BACKEND"),
    ("app/resource_manager.py", "WHISPER_GPU"),
    ("app/resource_manager.py", "WHISPER_VRAM_MB"),
    ("app/routes/admin.py", "LLAMA_SWAP_URL"),
    ("app/tasks/dry_load.py", "LLAMA_SWAP_URL"),
    ("app/tasks/health_monitor.py", "LLAMA_SWAP_URL"),
    ("app/vram_estimate.py", "LLAMACP_BACKEND"),
    ("app/vram_estimate.py", "LLAMA_SWAP_URL"),
}


def _scanned_reads() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for source in ("app", "modules"):
        for path in (APP_ROOT / source).rglob("*.py"):
            if path.name == "config.py":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for key in _READ_RE.findall(text):
                found.add((str(path.relative_to(APP_ROOT)), key))
    return found


@pytest.mark.unit
def test_no_panel_key_is_read_only_from_the_environment():
    panel = set(movable_keys())
    offenders = {
        (file, key) for file, key in _scanned_reads() if key in panel and (file, key) not in KNOWN_DIRECT_READS
    }
    assert not offenders, (
        "these read a panel-editable setting from the environment, so saving it in the panel "
        f"would change nothing: {sorted(offenders)}"
    )


@pytest.mark.unit
def test_the_allowlist_does_not_rot():
    # A converted call site must leave the allowlist, otherwise the ratchet
    # silently covers code that no longer needs it.
    still_there = _scanned_reads()
    stale = {(file, key) for file, key in KNOWN_DIRECT_READS if (file, key) not in still_there}
    assert not stale, f"remove these from KNOWN_DIRECT_READS, they are converted: {sorted(stale)}"


@pytest.mark.unit
def test_every_panel_key_has_a_config_entry(app):
    missing = [key for key in movable_keys() if key not in app.config]
    assert not missing, f"panel keys with no app.config entry: {missing}"


@pytest.mark.unit
def test_bootstrap_keys_are_not_also_panel_keys():
    assert not (set(BOOTSTRAP_KEYS) & set(movable_keys()))
