#!/usr/bin/env python3
"""
HTTP proxy for SuperLocalMemory daemon with per-user database isolation.

For requests with a ``profile`` parameter:
  - /recall reads from the user's private SQLite (fast, ~1ms)
  - /remember saves to both the daemon (shared) AND the user's private DB (async)

For requests without ``profile``: all forwarded to the daemon.
"""

import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

from flask import Flask, jsonify, request

DAEMON_URL = "http://localhost:8765"
SLM_DATA_DIR = "/app/data/slm"

app = Flask(__name__)


# ── Daemon helpers (shared DB) ───────────────────────────────────────


def _daemon_get(path: str) -> dict:
    try:
        resp = urllib.request.urlopen(f"{DAEMON_URL}{path}", timeout=30)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"daemon HTTP {e.code}: {e.read().decode()}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _daemon_post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{DAEMON_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=300)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"daemon HTTP {e.code}: {e.read().decode()}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Per-user SQLite helpers ──────────────────────────────────────────


def _user_db_path(profile: str) -> str | None:
    """Return path to the user's SLM SQLite database, or None."""
    path = os.path.join(SLM_DATA_DIR, profile, ".superlocalmemory", "memory.db")
    return path if os.path.isfile(path) else None


def _recall_from_user_db(profile: str, limit: int = 5) -> list[dict] | None:
    """Read latest active facts from the user's private SLM database.

    Deduplicates by content — if the same text appears multiple times
    (common from SLM import), only the most recent copy is kept.
    Fetches ``limit × 3`` rows internally to collect enough unique facts.

    Returns a list of dicts with keys ``content``, ``score``, ``fact_id``,
    ``created_at``, or ``None`` if the database is missing.
    """
    db_path = _user_db_path(profile)
    if not db_path:
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
        rows = conn.execute(
            "SELECT content, confidence, fact_id, created_at "
            "FROM atomic_facts "
            "WHERE lifecycle = 'active' "
            "AND LENGTH(content) <= 200 "
            "ORDER BY created_at DESC LIMIT ?",
            (limit * 3,),
        ).fetchall()
        conn.close()

        seen: set[str] = set()
        unique: list[dict] = []
        for r in rows:
            norm = r[0].strip() if r[0] else ""
            if norm in seen:
                continue
            seen.add(norm)
            unique.append(
                {
                    "content": r[0],
                    "score": r[1] if r[1] is not None else 0.5,
                    "fact_id": r[2],
                    "created_at": r[3],
                }
            )
            if len(unique) >= limit:
                break
        min_score = float(os.environ.get("SLM_MIN_SCORE", "0.3"))
        unique = [r for r in unique if r.get("score", 0) >= min_score]
        return unique
    except Exception as e:
        app.logger.warning(f"SLM recall from user DB failed for {profile}: {e}")
        return None


def _semantic_recall_from_user_db(query: str, limit: int, profile: str) -> list[dict] | None:
    """Full semantic recall via subprocess ``slm recall``.

    Slower (~2-5s) but uses SLM's multi-channel retrieval (semantic,
    BM25, entity graph, temporal). Runs with the user's HOME for
    per-database isolation.
    """
    home_dir = os.path.join(SLM_DATA_DIR, profile)
    env = os.environ.copy()
    env["HOME"] = home_dir
    try:
        result = subprocess.run(
            ["slm", "recall", query, "--json", "--limit", str(limit)],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if result.returncode != 0:
            app.logger.warning(f"SLM semantic recall subprocess failed: {result.stderr[:200]}")
            return None
        data = json.loads(result.stdout)
        raw_results = data.get("data", {}).get("results", [])
        seen: set[str] = set()
        unique: list[dict] = []
        for r in raw_results:
            norm = (r.get("content") or "").strip()
            if norm in seen:
                continue
            seen.add(norm)
            unique.append(
                {
                    "content": r.get("content", ""),
                    "score": r.get("score", 0),
                    "fact_id": r.get("fact_id", ""),
                    "created_at": r.get("created_at", ""),
                }
            )
            if len(unique) >= limit:
                break
        min_score = float(os.environ.get("SLM_MIN_SCORE", "0.3"))
        unique = [r for r in unique if r.get("score", 0) >= min_score]
        return unique
    except Exception as e:
        app.logger.warning(f"SLM semantic recall exception: {e}")
        return None


def _remember_to_user_db(text: str, metadata: dict | None, profile: str) -> None:
    """Save a fact to the user's private SLM database via subprocess.

    Runs in a background thread — does not block the HTTP response.
    The user's ``HOME`` is set to their isolated data directory.
    """
    home_dir = os.path.join(SLM_DATA_DIR, profile)
    env = os.environ.copy()
    env["HOME"] = home_dir
    with contextlib.suppress(Exception):
        subprocess.run(
            ["slm", "remember", text, "--json", "--sync"],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )


# ── Routes ───────────────────────────────────────────────────────────


@app.route("/health")
def health():
    try:
        resp = urllib.request.urlopen(f"{DAEMON_URL}/health", timeout=5)
        data = json.loads(resp.read().decode())
        return jsonify({"status": "ok", "service": "superlocalmemory", "daemon": data.get("status")})
    except Exception:
        return jsonify({"status": "ok", "service": "superlocalmemory", "daemon": "unreachable"})


@app.route("/remember", methods=["POST"])
def remember():
    """Store a fact — forward to daemon; also persist to per-user DB async."""
    data = request.get_json(force=True)
    text = data.get("text", "")
    if not text:
        return jsonify({"success": False, "error": "Missing text"}), 400

    meta = data.get("metadata", {})
    profile = data.get("profile")

    if profile:
        meta["profile"] = profile

    result = _daemon_post(
        "/remember?wait=true",
        {
            "content": text,
            "tags": "",
            "metadata": meta,
        },
    )

    if profile:
        t = threading.Thread(
            target=_remember_to_user_db,
            args=(text, meta, profile),
            daemon=True,
        )
        t.start()
        if not result.get("ok"):
            app.logger.warning(
                f"Daemon remember failed for {profile}, but per-user save was dispatched: {result.get('error')}"
            )
        return jsonify(
            {
                "success": True,
                "fact_ids": result.get("fact_ids", []),
                "note": "saved to per-user database" if not result.get("ok") else "",
            }
        )

    return jsonify(
        {
            "success": result.get("ok", False),
            "fact_ids": result.get("fact_ids", []),
            "error": result.get("error", ""),
        }
    )


@app.route("/recall", methods=["POST"])
def recall():
    """Retrieve relevant facts.

    With ``profile`` — read directly from the user's private SQLite (fast, ~1ms).
    Without ``profile`` — forward to the daemon (shared database).
    """
    data = request.get_json(force=True)
    query = data.get("query", "")
    limit = data.get("limit", 5)
    profile = data.get("profile")
    semantic = data.get("semantic", False)

    if profile:
        if semantic:
            results = _semantic_recall_from_user_db(query, limit, profile)
            if not results:
                results = _recall_from_user_db(profile, limit)
        else:
            results = _recall_from_user_db(profile, limit)
        # profile set → read ONLY from user DB, never fall through to daemon
        return jsonify({"success": True, "data": {"results": results or []}})

    if not query:
        return jsonify({"success": False, "error": "Missing query"}), 400

    result = _daemon_get(f"/recall?q={urllib.parse.quote(query)}&limit={limit}&fast=true")
    results = []
    for r in result.get("results", []):
        results.append(
            {
                "content": r.get("content", ""),
                "score": r.get("score", 0),
                "confidence": r.get("confidence", 0),
                "fact_id": r.get("fact_id", ""),
            }
        )
    return jsonify(
        {
            "success": result.get("ok", False),
            "data": {"results": results},
            "error": result.get("error", ""),
        }
    )


@app.route("/forget", methods=["POST"])
def forget():
    return jsonify({"success": True, "note": "forget not supported via daemon"})


@app.route("/list", methods=["POST"])
def list_facts():
    """List user's facts — read from per-user DB if profile is provided."""
    data = request.get_json(force=True)
    limit = data.get("limit", 20)
    profile = data.get("profile")

    if profile:
        results = _recall_from_user_db(profile, limit)
        if results is not None:
            return jsonify({"success": True, "data": {"results": results}})

    return jsonify({"success": True, "data": {"results": []}})


@app.route("/", methods=["GET"])
def root():
    return jsonify({"service": "superlocalmemory", "daemon_proxy": True})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
