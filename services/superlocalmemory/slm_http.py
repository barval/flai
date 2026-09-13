#!/usr/bin/env python3
"""
HTTP proxy for SuperLocalMemory daemon with per-user database isolation.

For requests with a ``profile`` parameter:
  - /recall reads from the user's private SQLite (fast, ~1ms)
  - /remember saves to both the daemon (shared) AND the user's private DB (async)

For requests without ``profile``: all forwarded to the daemon.

Endpoints:
  - /cleanup-memories removes orphaned memories for one or all users
  - A periodic background thread runs every hour to clean orphaned memories automatically
"""

import contextlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time as _time
import urllib.error
import urllib.parse
import urllib.request

from flask import Flask, jsonify, request

DAEMON_URL = "http://localhost:8765"
SLM_DATA_DIR = "/app/data/slm"

# ── Hybrid recall (keyword → semantic → latest) ────────────────────────
# Borrowed technique from mem0 v3: keyword-first fast path avoids the
# daemon's 300-800 ms embedding call on most turns.

_SLH_KEYWORD_THRESHOLD = float(os.environ.get("SLM_HYBRID_KEYWORD_THRESHOLD", "0.4"))
_SLH_RECENT_BOOST = float(os.environ.get("SLM_HYBRID_RECENT_BOOST", "1.5"))

_STOPWORDS_RU = frozenset(
    {
        "и",
        "в",
        "во",
        "не",
        "что",
        "он",
        "на",
        "я",
        "с",
        "со",
        "как",
        "а",
        "то",
        "все",
        "она",
        "так",
        "его",
        "но",
        "да",
        "ты",
        "к",
        "у",
        "же",
        "вы",
        "за",
        "бы",
        "по",
        "только",
        "её",
        "мне",
        "было",
        "вот",
        "от",
        "меня",
        "ещё",
        "нет",
        "о",
        "из",
        "ему",
        "теперь",
        "когда",
        "даже",
        "ли",
        "если",
        "уже",
        "или",
        "ни",
        "быть",
        "был",
        "него",
        "до",
        "вас",
        "нибудь",
        "опять",
        "уж",
        "вам",
        "ведь",
        "там",
        "потом",
        "себя",
        "ничего",
        "ей",
        "может",
        "они",
        "тут",
        "где",
        "есть",
        "надо",
        "ней",
        "для",
        "мы",
        "тебя",
        "их",
        "чем",
        "была",
        "сам",
        "чтоб",
        "без",
        "будто",
        "чего",
        "раз",
        "тоже",
        "себе",
        "под",
        "будет",
        "ж",
        "тогда",
        "кто",
        "этот",
        "того",
        "потому",
        "этого",
        "какой",
        "совсем",
        "ним",
        "здесь",
        "этом",
        "один",
        "почти",
        "мой",
        "тем",
        "чтобы",
        "нее",
        "сейчас",
        "были",
        "куда",
        "зачем",
        "всех",
        "никогда",
        "можно",
        "при",
        "наконец",
        "два",
        "об",
        "другой",
        "хоть",
        "после",
        "над",
        "больше",
        "тот",
        "через",
        "эти",
        "нас",
        "про",
        "всего",
        "них",
        "какая",
        "много",
        "разве",
        "три",
        "эту",
        "моя",
        "впрочем",
        "хорошо",
        "свою",
        "этой",
        "перед",
        "иногда",
        "лучше",
        "чуть",
        "том",
        "нельзя",
        "такой",
        "им",
        "более",
        "всегда",
        "конечно",
        "всю",
        "между",
    }
)

_STOPWORDS_EN = frozenset(
    {
        "i",
        "me",
        "my",
        "myself",
        "we",
        "our",
        "ours",
        "ourselves",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
        "he",
        "him",
        "his",
        "himself",
        "she",
        "her",
        "hers",
        "herself",
        "it",
        "its",
        "itself",
        "they",
        "them",
        "their",
        "theirs",
        "themselves",
        "what",
        "which",
        "who",
        "whom",
        "this",
        "that",
        "these",
        "those",
        "am",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "having",
        "do",
        "does",
        "did",
        "doing",
        "a",
        "an",
        "the",
        "and",
        "but",
        "if",
        "or",
        "because",
        "as",
        "until",
        "while",
        "of",
        "at",
        "by",
        "for",
        "with",
        "about",
        "against",
        "between",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "to",
        "from",
        "up",
        "down",
        "in",
        "out",
        "on",
        "off",
        "over",
        "under",
        "again",
        "further",
        "then",
        "once",
        "here",
        "there",
        "when",
        "where",
        "why",
        "how",
        "all",
        "both",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "nor",
        "not",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "s",
        "t",
        "can",
        "will",
        "just",
        "don",
        "should",
        "now",
        "d",
        "ll",
        "m",
        "o",
        "re",
        "ve",
        "y",
        "ain",
        "aren",
        "couldn",
        "didn",
        "doesn",
        "hadn",
        "hasn",
        "haven",
        "isn",
        "ma",
        "mightn",
        "mustn",
        "needn",
        "shan",
        "shouldn",
        "wasn",
        "weren",
        "won",
        "wouldn",
    }
)

_TOKEN_RE = re.compile(r"[a-zа-яё0-9]{2,}", re.I)

_TEMPORAL_WINDOWS = [
    (re.compile(r"\b(вчера|yesterday)\b", re.I), 2, 1),
    (re.compile(r"\b(позавчера|day before yesterday)\b", re.I), 3, 2),
    (re.compile(r"\b(на днях)\b", re.I), 5, 0),
    (re.compile(r"\b(недавно|в последнее время|recently|lately)\b", re.I), 30, 0),
    (re.compile(r"\b(на прошлой неделе|last week)\b", re.I), 14, 7),
    (re.compile(r"\b(на этой неделе|this week)\b", re.I), 7, 0),
]


def _tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens >= 2 chars, excluding stopwords."""
    lang = "ru" if any("\u0400" <= c <= "\u04ff" for c in text) else "en"
    stops = _STOPWORDS_RU if lang == "ru" else _STOPWORDS_EN
    return [m.group().lower() for m in _TOKEN_RE.finditer(text) if m.group().lower() not in stops]


def _keyword_score(query_tokens: list[str], content: str) -> float:
    """Fraction of query tokens found in content (0.0–1.0)."""
    if not query_tokens:
        return 0.0
    content_lower = content.lower()
    matched = sum(1 for t in query_tokens if t in content_lower)
    return matched / len(query_tokens)


def _recency_boost(created_at: int) -> float:
    """Newer facts get a slight score bonus (1.0–1.3)."""
    if not created_at or created_at <= 0:
        return 1.0
    now_ts = int(_time.time())
    age_days = max(1.0, (now_ts - created_at) / 86400.0)
    return 1.0 + 0.3 * (1.0 / (1.0 + age_days / 7.0))


def _time_window_from_query(query: str) -> tuple[int, int] | None:
    """Return (start_epoch, end_epoch) if temporal cue detected, else None."""
    now = int(_time.time())
    for pat, back_start, back_end in _TEMPORAL_WINDOWS:
        if pat.search(query):
            return (now - back_start * 86400, now - back_end * 86400)
    if re.search(r"\b(сегодня|today)\b", query, re.I):
        import datetime as _dt

        today_start = int(_dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        return (today_start, now)
    return None


def _hybrid_recall_from_user_db(query: str, limit: int, profile: str) -> list[dict] | None:
    """Keyword scan → semantic fallback → latest facts for per-user DB.

    When the per-user DB is missing, falls back to the daemon semantic path
    (then latest facts) so profile recall keeps working.

    Returns list of dicts with 'content', 'score', 'confidence',
    'fact_id', 'created_at' keys, or None if no data source is available.
    """
    db_path = _user_db_path(profile)
    if not db_path:
        # User DB unavailable (not yet populated) — fall back to the daemon
        # semantic path so profile recall keeps working, then latest facts.
        sem = _semantic_recall_from_user_db(query, limit, profile)
        if sem:
            return sem
        return _recall_from_user_db(profile, limit)

    query_tokens = _tokenize(query)
    if not query_tokens:
        return None

    time_window = _time_window_from_query(query)

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
        sql = (
            "SELECT content, confidence, fact_id, created_at "
            "FROM atomic_facts "
            "WHERE lifecycle = 'active' AND LENGTH(content) <= 200"
        )
        params: list[int] = []
        if time_window:
            sql += " AND created_at >= ? AND created_at <= ?"
            params.extend([time_window[0], time_window[1]])
        sql += " ORDER BY created_at DESC LIMIT 500"
        rows = conn.execute(sql, params).fetchall()
        conn.close()
    except Exception as e:
        app.logger.warning(f"Hybrid recall DB error for {profile}: {e}")
        return None

    if not rows:
        if time_window:
            sem = _semantic_recall_from_user_db(query, limit, profile)
            if sem:
                return sem
        return _recall_from_user_db(profile, limit)

    scored: list[tuple[float, str, float, str, int]] = []
    for r in rows:
        content = (r[0] or "").strip()
        kw = _keyword_score(query_tokens, content)
        if kw < _SLH_KEYWORD_THRESHOLD:
            continue
        combined = min(kw * _recency_boost(r[3]), 1.0)
        if combined >= _SLH_KEYWORD_THRESHOLD:
            scored.append((combined, content, r[1] if r[1] is not None else 0.5, r[2], r[3]))

    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        min_score = float(os.environ.get("SLM_MIN_SCORE", "0.3"))
        return [
            {"content": s[1], "score": s[0], "confidence": s[2], "fact_id": s[3], "created_at": s[4]}
            for s in scored[:limit]
            if s[0] >= min_score
        ]

    sem = _semantic_recall_from_user_db(query, limit, profile)
    if sem:
        return sem

    return _recall_from_user_db(profile, limit)


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
    """Full semantic recall via daemon's in-process engine (~300-800ms).

    The daemon already holds the embedding model in RAM.  Routing through
    the daemon avoids the cold-start penalty of spawning a fresh
    ``slm recall`` subprocess (~30s for PyTorch + sentence-transformers).
    """
    try:
        result = _daemon_get(f"/recall?q={urllib.parse.quote(query)}&limit={limit}&fast=false")
        if not result.get("ok"):
            return None
        raw_results = result.get("results", [])
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
        app.logger.warning(f"SLM semantic recall via daemon failed: {e}")
        return None


def _cleanup_memories_for_user(profile: str) -> dict:
    """Remove orphaned rows from ``memories`` table.

    A memory is orphaned when none of its ``atomic_facts`` has
    ``lifecycle = 'active'``.  Deleting from ``memories`` cascades to
    ``atomic_facts`` (FK ON DELETE CASCADE), which is safe because all
    related facts are already archived.

    Returns a dict with counts for logging.
    """
    db_path = _user_db_path(profile)
    if not db_path:
        return {"deleted": 0, "error": "db not found"}

    try:
        conn = sqlite3.connect(db_path)
        before = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        cursor = conn.execute(
            """
            DELETE FROM memories
            WHERE memory_id NOT IN (
                SELECT DISTINCT memory_id
                FROM atomic_facts
                WHERE lifecycle = 'active'
            )
            """
        )
        deleted = cursor.rowcount
        after = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        conn.commit()
        conn.close()
        return {"deleted": deleted, "before": before, "after": after}
    except Exception as e:
        return {"deleted": 0, "error": str(e)}


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
            results = _hybrid_recall_from_user_db(query, limit, profile)
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


@app.route("/delete", methods=["POST"])
def delete_fact():
    """Delete a specific fact by ID from the user's private SQLite database."""
    data = request.get_json(force=True)
    fact_id = data.get("id", "")
    profile = data.get("profile")

    if not fact_id or not profile:
        return jsonify({"success": False, "error": "id and profile required"}), 400

    db_path = _user_db_path(profile)
    if not db_path:
        return jsonify({"success": False, "error": "user database not found"}), 404

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "UPDATE atomic_facts SET lifecycle = 'archived' WHERE fact_id = ? AND lifecycle = 'active'",
            (fact_id,),
        )
        conn.commit()
        deleted = cursor.rowcount
        conn.close()
        return jsonify({"success": True, "deleted": deleted})
    except Exception as e:
        app.logger.warning(f"SLM delete_fact failed for {profile}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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


@app.route("/cleanup-memories", methods=["POST"])
def cleanup_memories():
    """Remove orphaned rows from ``memories`` table for one or all users.

    A memory is orphaned when none of its ``atomic_facts`` has
    ``lifecycle = 'active'``.

    POST body: ``{"profile": "valery"}`` or ``{}`` (all users).
    """
    data = request.get_json(force=True) if request.data else {}
    profile = data.get("profile")

    if profile:
        result = _cleanup_memories_for_user(profile)
        return jsonify({"success": True, "profile": profile, **result})

    # All users
    results = {}
    if os.path.isdir(SLM_DATA_DIR):
        for entry in os.listdir(SLM_DATA_DIR):
            if os.path.isdir(os.path.join(SLM_DATA_DIR, entry)):
                results[entry] = _cleanup_memories_for_user(entry)

    total_deleted = sum(r.get("deleted", 0) for r in results.values())
    return jsonify({"success": True, "total_deleted": total_deleted, "profiles": results})


@app.route("/similarity", methods=["POST"])
def similarity_check():
    """Return similarity score between candidate text and closest existing fact.

    POST body: ``{"text": "...", "profile": "valery"}``.
    Returns ``{"success": true, "max_similarity": 0.87, "closest": "..."}``
    or ``{"success": true, "max_similarity": 0.0, "closest": null}`` when no
    facts exist yet.
    """
    data = request.get_json(force=True) if request.data else {}
    text = (data.get("text") or "").strip()
    profile = data.get("profile", "")
    if not text:
        return jsonify({"success": False, "error": "text required"}), 400

    results = _semantic_recall_from_user_db(text, limit=1, profile=profile)
    if not results:
        return jsonify({"success": True, "max_similarity": 0.0, "closest": None})

    return jsonify(
        {
            "success": True,
            "max_similarity": results[0].get("score", 0.0),
            "closest": results[0].get("content", ""),
        }
    )


@app.route("/", methods=["GET"])
def root():
    return jsonify({"service": "superlocalmemory", "daemon_proxy": True})


def _periodic_cleanup(interval: int = 3600) -> None:
    """Background thread: clean orphaned memories every *interval* seconds."""
    import time

    while True:
        time.sleep(interval)
        try:
            if not os.path.isdir(SLM_DATA_DIR):
                continue
            for entry in os.listdir(SLM_DATA_DIR):
                if os.path.isdir(os.path.join(SLM_DATA_DIR, entry)):
                    result = _cleanup_memories_for_user(entry)
                    deleted = result.get("deleted", 0)
                    if deleted:
                        app.logger.info(f"Periodic cleanup for {entry}: deleted {deleted} orphaned memories")
        except Exception as e:
            app.logger.warning(f"Periodic cleanup failed: {e}")


if __name__ == "__main__":
    cleanup_thread = threading.Thread(target=_periodic_cleanup, args=(3600,), daemon=True)
    cleanup_thread.start()

    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
