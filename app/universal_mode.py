"""One universal model instead of a small multimodal model plus a reasoning model.

FLAI has always run a pair: a small always-resident multimodal model for the
router, vision and short chat, and a large reasoning model that is loaded just
in time. Measured on a 16 GB card, the pair cannot be resident together
(8 642 + 13 048 MB against 16 311), so every reasoning answer pays a 14 s model
swap, and the multimodal model is reloaded afterwards.

A single model that is both multimodal and reasoning removes that swap. It is
stored as a ``model_configs`` row whose module is ``universal``: a non-empty
``model_name`` means the installation runs in single-model mode, an empty one
keeps the two-model pair. Both roles then resolve to this one row (see
``app.model_config.get_model_config``), so every caller — the router, vision,
chat, the reasoning worker, the task planner — asks for the same weights, and
llama-swap is given one entry to run instead of two.

The mode is a database row, not an environment variable: switching it in the
admin panel is the whole procedure, and no deployment flag or compose file
depends on it.
"""

from __future__ import annotations

import glob
import logging
import mmap
import os
import struct
import time
from typing import Any

logger = logging.getLogger(__name__)

MODELS_DIR = os.getenv("MODELS_DIR", "/models")

UNIVERSAL_MODULE = "universal"
# Roles that collapse into the universal model when the mode is on.
ROLE_MODULES = ("multimodal", "reasoning")


def find_mmproj(model_path: str | None, model_name: str | None = None) -> str | None:
    """Locate the vision projector that belongs to a model file.

    Two layouts are accepted:

    1. a projector whose file name carries the model's own name —
       ``mmproj-<model>-F16.gguf`` — in the model's directory or in the models
       root, which is how a deployment downloads one projector per model;
    2. a projector that sits alone in the model's **own subdirectory**, the
       layout llama.cpp expects (``<model>/mmproj-F16.gguf``) and the one the
       Qwen3-VL family ships with.

    A projector in a shared flat directory is never accepted without a name
    match: with two models there, the first sorted file attached gemma's encoder
    to Qwen and made a text model look multimodal. Returns ``None`` when the
    model has no projector, which is also the test for "cannot be universal".
    """
    if not model_path:
        return None

    stem = os.path.basename(model_path)
    if stem.endswith(".gguf"):
        stem = stem[: -len(".gguf")]
    if model_name:
        stem = model_name[: -len(".gguf")] if model_name.endswith(".gguf") else model_name

    base_dir = os.path.dirname(model_path)

    # 1. Named projector, next to the model first, then in the models root.
    for directory in (base_dir, MODELS_DIR):
        named = sorted(glob.glob(os.path.join(directory, f"mmproj*{stem}*.gguf")))
        if named:
            return named[0]

    # 2. An unnamed projector that can only belong to this model: it is alone in
    # the model's own directory, which is not the shared models root.
    if os.path.abspath(base_dir) != os.path.abspath(MODELS_DIR):
        local = sorted(glob.glob(os.path.join(base_dir, "mmproj*.gguf")))
        if len(local) == 1:
            return local[0]

    return None


# A model that only sees is not a universal model: it would answer every
# reasoning request with a chat model's depth. The chat template is the honest
# signal — Qwen3.6, Qwen3.8 and gemma-4 carry a thinking phase there, while a
# dedicated vision model such as Qwen3-VL-8B-Instruct does not.
_REASONING_TEMPLATE_MARKERS = ("enable_thinking", "preserve_thinking", "reasoning_content", "thinking")
_VISION_FIRST_ARCH = ("qwen3vl", "qwen2vl", "qwen3vlmoe", "llava", "mllama", "pixtral")

_REASONING_CACHE: dict[str, bool] = {}
_TEMPLATE_CACHE: dict[str, tuple[tuple[int, int], str | None]] = {}
_AVAILABLE_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_AVAILABLE_TTL_S = 30.0


def invalidate_available_cache() -> None:
    """Drop the candidate list (a model was added, removed or chosen)."""
    _AVAILABLE_CACHE.clear()


def _chat_template(path: str) -> str | None:
    """The model's chat template, read from the GGUF key/value header only.

    ``GGUFReader`` walks the whole tensor-info table, which measured 7–11 s on an
    11–24 GB file — the admin tab waited that long for every candidate it had to
    classify. The template lives in the key/value block at the head of the file,
    so this reads it directly and skips every other value by its declared size.
    """
    try:
        stat = os.stat(path)
    except OSError:
        return None
    cached = _TEMPLATE_CACHE.get(path)
    if cached and cached[0] == (stat.st_mtime_ns, stat.st_size):
        return cached[1]

    template: str | None = None
    try:
        with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            template = _read_chat_template_from_map(mm)
    except Exception as exc:  # noqa: BLE001 — an unreadable header is a "no"
        logger.debug("universal: cannot read the chat template of %s: %s", path, exc)
        template = None

    _TEMPLATE_CACHE[path] = ((stat.st_mtime_ns, stat.st_size), template)
    return template


def _read_chat_template_from_map(mm) -> str | None:
    """Walk the GGUF key/value section of ``mm`` and return the chat template."""
    cursor = 0

    def u32(pos: int) -> int:
        return int(struct.unpack_from("<I", mm, pos)[0])

    def u64(pos: int) -> int:
        return int(struct.unpack_from("<Q", mm, pos)[0])

    def text(pos: int) -> tuple[str, int]:
        length = u64(pos)
        start = pos + 8
        return mm[start : start + length].decode("utf-8", "replace"), start + length

    if mm[:4] != b"GGUF":
        return None
    kv_count = u64(16)
    cursor = 24

    for _ in range(kv_count):
        key, cursor = text(cursor)
        value_type = u32(cursor)
        cursor += 4

        if value_type == 8:  # string
            value, cursor = text(cursor)
            if key.endswith("chat_template"):
                return value
            continue
        if value_type == 9:  # array
            element_type = u32(cursor)
            count = u64(cursor + 4)
            cursor += 12
            sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
            if element_type == 8:
                for _ in range(count):
                    length = u64(cursor)
                    cursor += 8 + length
            elif element_type in sizes:
                cursor += sizes[element_type] * count
            else:
                return None
            continue
        fixed = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
        if value_type not in fixed:
            return None
        cursor += fixed[value_type]
    return None


def supports_reasoning(model_name: str, model_path: str | None = None) -> bool:
    """Whether the model reasons, rather than only describing what it sees."""
    path = model_path or model_path_for(model_name)
    if not path:
        return False
    key = os.path.basename(path)
    if key in _REASONING_CACHE:
        return _REASONING_CACHE[key]

    template = _chat_template(path)
    if template is not None:
        low = template.lower()
        result = any(marker in low for marker in _REASONING_TEMPLATE_MARKERS)
    else:
        # An unreadable header: fall back to what the name and architecture say.
        # A vision-first family is excluded, everything else is a chat model.
        low_name = model_name.lower()
        result = not any(marker in low_name for marker in _VISION_FIRST_ARCH) and not any(
            marker in low_name for marker in ("-vl-", "vision", "llava")
        )

    _REASONING_CACHE[key] = result
    return result


def universal_row() -> dict[str, Any] | None:
    """The raw ``universal`` row, or ``None`` when the table has no such row."""
    from app.model_config import get_model_config

    try:
        return get_model_config(UNIVERSAL_MODULE, _raw=True)
    except Exception as exc:  # noqa: BLE001 — a missing row is a valid state
        logger.debug("universal: cannot read the row: %s", exc)
        return None


def active_model_name() -> str | None:
    """Model name when single-model mode is on, otherwise ``None``."""
    row = universal_row()
    if not row:
        return None
    name = str(row.get("model_name") or "").strip()
    return name or None


def is_enabled() -> bool:
    return active_model_name() is not None


def entry_name(module: str) -> str:
    """llama-swap entry that serves ``module``.

    In single-model mode both roles are served by one entry, so anything that
    talks to llama-swap about a module (unload, "is it running") must use this
    name instead of the module name.
    """
    if module in ROLE_MODULES and is_enabled():
        return UNIVERSAL_MODULE
    return module


def context_length() -> int | None:
    row = universal_row()
    if not row:
        return None
    value = row.get("context_length")
    return int(value) if value else None


def model_path_for(name: str) -> str | None:
    """Resolve a model name to a path the same way the config generator does."""
    from app.database import _find_gguf_file

    return _find_gguf_file(name, MODELS_DIR)


def mmproj_for(name: str) -> str | None:
    return find_mmproj(model_path_for(name), name)


def available_models() -> list[dict[str, Any]]:
    """Every model that can serve as the universal model.

    Two things are required, because a universal model replaces both halves of
    the pair: a vision projector next to the file (multimodality in llama.cpp is
    that file, and without it the router and every image path break), and a chat
    template with a thinking phase (otherwise the model that answers images is a
    chat model's depth, not a reasoning model's — measured on Qwen3-VL-8B, which
    became the only "universal" candidate on a host where it was the only model
    with a projector).
    """
    from app.utils import get_gguf_models_cached

    now = time.monotonic()
    cached_list = _AVAILABLE_CACHE.get(MODELS_DIR)
    if cached_list and now - cached_list[0] < _AVAILABLE_TTL_S:
        return list(cached_list[1])

    try:
        cache = get_gguf_models_cached(MODELS_DIR)
    except Exception:  # noqa: BLE001 — an unreadable cache must not empty the UI
        cache = {}

    found: dict[str, dict[str, Any]] = {}
    for pattern in (os.path.join(MODELS_DIR, "*.gguf"), os.path.join(MODELS_DIR, "*", "*.gguf")):
        for path in sorted(glob.glob(pattern)):
            base = os.path.basename(path)
            if base.startswith("mmproj") or "mmproj" in base.lower():
                continue
            name = base[: -len(".gguf")]
            projector = find_mmproj(path, name)
            if not projector:
                continue
            if not supports_reasoning(name, path):
                continue
            meta = cache.get(name, {}) or {}
            found[name] = {
                "name": name,
                "mmproj": os.path.basename(projector),
                "size_gb": round(os.path.getsize(path) / (1024**3), 2),
                "architecture": meta.get("architecture") or "",
                "context_length": meta.get("context_length") or 0,
            }
    result = sorted(found.values(), key=lambda item: item["name"].lower())
    _AVAILABLE_CACHE[MODELS_DIR] = (now, result)
    return list(result)


def set_model(name: str | None) -> None:
    """Turn single-model mode on (``name``) or off (``None``/empty)."""
    from app.database import get_db
    from app.model_config import invalidate_model_config_cache

    clean = (name or "").strip()
    if clean.endswith(".gguf"):
        clean = clean[: -len(".gguf")]

    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO model_configs (module, model_name, context_length, temperature, top_p, timeout, service_url, repeat_penalty)
            VALUES (%s, %s, NULL, 0.7, 0.9, 300, 'http://flai-llamacpp:8033', 1.15)
            ON CONFLICT (module) DO UPDATE SET model_name = EXCLUDED.model_name, updated_at = CURRENT_TIMESTAMP
            """,
            (UNIVERSAL_MODULE, clean or None),
        )
    invalidate_model_config_cache()
