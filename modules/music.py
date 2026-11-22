"""ACE-Step 1.5 client: the only thing FLAI knows about the music sidecar.

The model itself runs in its own container (`flai-music`, profile `with-music`)
because it pins its own PyTorch stack. This module speaks the sidecar's REST
contract and never imports torch.

Measured on the reference card (RTX 5060 Ti, 16 311 MiB): idle 5.5 GB, peak
6.9 GB, a 30 s Russian song in 10-12 s with the 2B turbo DiT and the 0.6B LM.
Lyrics must carry `[verse]`/`[chorus]` structure: measured, the same lines
without structure tags produced no intelligible vocal at all.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import requests

from app.config import setting

HEALTH_TTL_S = 60.0
POLL_INTERVAL_S = 2.0


class MusicError(RuntimeError):
    """A failure the caller turns into a localized sentence."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def normalize_lyrics(lyrics: str) -> str:
    """Give the model the structure it needs; it sings nothing without it."""
    text = (lyrics or "").strip()
    if not text:
        return ""
    if "[" in text and "]" in text:
        return text
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    half = max(1, len(lines) // 2)
    first = "\n".join(lines[:half])
    second = "\n".join(lines[half:]) if lines[half:] else ""
    if not second:
        return f"[verse]\n{first}"
    return f"[verse]\n{first}\n[chorus]\n{second}"


def lyric_lines(lyrics: str) -> int:
    """Count singable lines, ignoring structure tags and blanks."""
    count = 0
    for line in (lyrics or "").splitlines():
        stripped = line.strip()
        if not stripped or (stripped.startswith("[") and stripped.endswith("]")):
            continue
        count += 1
    return count


def plain_lyrics(lyrics: str) -> str:
    """Lyrics without [verse]/[chorus] markers — what the person should read."""
    kept = [
        line
        for line in (lyrics or "").splitlines()
        if not (line.strip().startswith("[") and line.strip().endswith("]"))
    ]
    return "\n".join(kept).strip()


def fit_duration(lyrics: str, requested: int, default: int = 30, per_line: int = 5) -> int:
    """A duration that fits the words: ~5 s per line, never below the default."""
    lines = lyric_lines(lyrics)
    needed = max(default, lines * per_line)
    return max(int(requested or 0), needed)


class MusicModule:
    """Thin HTTP client for the ACE-Step REST API."""

    def __init__(self, app: Any = None) -> None:
        self.app = app
        self._lock = threading.Lock()
        self._available: bool | None = None
        self._checked_at = 0.0
        self._loaded = False

    # ── configuration ────────────────────────────────────────────

    @property
    def base_url(self) -> str:
        return str(setting("MUSIC_URL", "http://flai-music:8001")).rstrip("/")

    @property
    def timeout_s(self) -> int:
        return int(setting("MUSIC_TIMEOUT_S", 900))

    @property
    def model(self) -> str:
        return str(setting("MUSIC_MODEL", "acestep-v15-turbo"))

    @property
    def lm_model(self) -> str:
        return str(setting("MUSIC_LM_MODEL", "acestep-5Hz-lm-0.6B"))

    @property
    def default_duration_s(self) -> int:
        return int(setting("MUSIC_DEFAULT_DURATION_S", 30))

    @property
    def max_duration_s(self) -> int:
        return int(setting("MUSIC_MAX_DURATION_S", 600))

    # ── health ───────────────────────────────────────────────────

    def is_available(self, force: bool = False) -> bool:
        """True when the sidecar answers /health; cached for a minute."""
        now = time.time()
        if not force and self._available is not None and now - self._checked_at < HEALTH_TTL_S:
            return self._available
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=2)
            ok = resp.status_code == 200
        except Exception:
            ok = False
        self._available = ok
        self._checked_at = now
        return ok

    def ensure_loaded(self) -> bool:
        """Load DiT + LM once. Loading takes ~20 s and is part of the task budget."""
        if self._loaded:
            return True
        with self._lock:
            if self._loaded:
                return True
            try:
                resp = requests.post(
                    f"{self.base_url}/v1/init",
                    json={
                        "model": self.model,
                        "init_llm": True,
                        "lm_model_path": self.lm_model,
                        "slot": 1,
                    },
                    timeout=max(120, self.timeout_s),
                )
                payload = resp.json()
            except Exception as exc:
                raise MusicError("unavailable", str(exc)) from exc
            if resp.status_code != 200 or not (payload or {}).get("data"):
                raise MusicError("unavailable", str(payload)[:300])
            self._loaded = True
            return True

    # ── generation ───────────────────────────────────────────────

    def generate(
        self,
        prompt: str,
        lyrics: str,
        duration_s: int | None = None,
        language: str = "ru",
        out_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Render one song and download the audio file."""
        duration = int(duration_s or self.default_duration_s)
        if duration < 10 or duration > self.max_duration_s:
            raise MusicError("duration", f"{duration} outside 10..{self.max_duration_s}")

        structured = normalize_lyrics(lyrics)
        if not structured:
            raise MusicError("lyrics", "empty lyrics")

        if not self.is_available(force=True):
            raise MusicError("unavailable", self.base_url)
        self.ensure_loaded()

        body = {
            "prompt": prompt or "russian pop ballad, warm female vocal",
            "lyrics": structured,
            "vocal_language": language,
            "audio_duration": duration,
            "audio_format": "wav",
            "thinking": True,
            "batch_size": 1,
            "inference_steps": 8,
            "model": self.model,
            "lm_model_path": self.lm_model,
            "lm_backend": "pt",
            "use_random_seed": True,
        }

        started = time.time()
        try:
            resp = requests.post(f"{self.base_url}/release_task", json=body, timeout=60)
            resp.raise_for_status()
            data = (resp.json() or {}).get("data")
        except Exception as exc:
            raise MusicError("failed", str(exc)) from exc
        task_id = data.get("task_id") if isinstance(data, dict) else str(data)
        if not task_id:
            raise MusicError("failed", "no task id")

        row = self._wait(task_id)
        file_url = row.get("file") or ""
        if not file_url:
            raise MusicError("failed", "no audio in result")

        target = Path(out_dir or Path(".")) / f"{uuid.uuid4().hex}.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        url = file_url if file_url.startswith("http") else f"{self.base_url}{file_url}"
        try:
            with requests.get(url, stream=True, timeout=120) as audio:
                audio.raise_for_status()
                with target.open("wb") as fh:
                    for chunk in audio.iter_content(1 << 16):
                        fh.write(chunk)
        except Exception as exc:
            raise MusicError("failed", str(exc)) from exc

        return {
            "path": str(target),
            "seconds": round(time.time() - started, 2),
            "model": row.get("dit_model") or self.model,
            "seed": row.get("seed_value") or "",
            "bytes": target.stat().st_size,
        }

    # ── internals ────────────────────────────────────────────────

    def _wait(self, task_id: str) -> dict[str, Any]:
        deadline = time.time() + self.timeout_s
        while time.time() < deadline:
            time.sleep(POLL_INTERVAL_S)
            try:
                resp = requests.post(
                    f"{self.base_url}/query_result",
                    json={"task_id_list": [task_id]},
                    timeout=60,
                )
                resp.raise_for_status()
                rows = (resp.json() or {}).get("data") or []
            except Exception as exc:
                raise MusicError("failed", str(exc)) from exc
            if not rows:
                continue
            row = rows[0]
            inner = _inner_rows(row)
            done = [r for r in inner if int(r.get("status") or 0) == 1 and r.get("file")]
            if done:
                return done[0]
            if int(row.get("status") or 0) == 2 or any(
                int(r.get("status") or 0) == 2 for r in inner
            ):
                raise MusicError("failed", str(row)[:300])
        raise MusicError("timeout", task_id)


def _inner_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    """The API wraps a JSON string whose payload is the per-file row list."""
    raw = row.get("result")
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]
