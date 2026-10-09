"""
Pytest fixtures and configuration for FLAI tests.

Provides isolated test environments with mocked external services.

Behaviour modes:
  - DATABASE_URL is set in env (CI)  → real PostgreSQL, no DB mocking
  - DATABASE_URL not set (local)     → in-memory mock DB via _MockDatabase
"""

import contextlib
import os
import re
import shutil
import tempfile
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ── Module-level: set DATABASE_URL before any app imports ──────────────
# (app/database.py raises ValueError if DATABASE_URL is absent at import)
_USE_MOCK_DB = "DATABASE_URL" not in os.environ
if _USE_MOCK_DB:
    os.environ["DATABASE_URL"] = "postgresql://localhost:5432/flai_test"

from app import create_app

# ── Helpers: mock external services ────────────────────────────────────


def stop_app_background_threads(flask_app: Any) -> None:
    """Stop the per-app daemon threads that create_app() starts.

    Every create_app() spawns an SLM merge watcher and (with the llama-swap
    backend) a crash-loop watchdog. Both poll forever and hold an app context,
    so without this a suite that builds hundreds of apps accumulates hundreds
    of live threads and their Redis clients. Best-effort: a test that
    deliberately skipped a thread must not fail the teardown.
    """
    with contextlib.suppress(Exception):
        from app import stop_slm_merge_watcher

        stop_slm_merge_watcher(flask_app, timeout=3)
    with contextlib.suppress(Exception):
        from app.tasks.health_monitor import stop_watchdog

        stop_watchdog(flask_app, timeout=3)


def create_mock_redis():
    mock_redis = MagicMock()
    mock_redis.blpop.return_value = None
    mock_redis.llen.return_value = 0
    mock_redis.hlen.return_value = 0
    mock_redis.rpush.return_value = 1
    mock_redis.scard.return_value = 0
    mock_redis.sadd.return_value = 1
    mock_redis.srem.return_value = 1
    mock_redis.hset.return_value = 1
    mock_redis.hdel.return_value = 1
    mock_redis.hgetall.return_value = {}
    mock_redis.hget.return_value = None
    mock_redis.lrange.return_value = []
    mock_redis.smembers.return_value = set()
    mock_redis.ping.return_value = True
    mock_redis.expire.return_value = True
    return mock_redis


def create_mock_llamacpp():
    mock_client = MagicMock()
    mock_client.chat.return_value = "Test response from llama-server"
    mock_client.call.return_value = "Test response from llama-server"
    mock_client.check_availability.return_value = True
    mock_client.get_embeddings.return_value = [[0.1] * 1024]
    mock_client.available = True
    return mock_client


def create_mock_qdrant():
    mock_client = MagicMock()
    mock_client.search.return_value = []
    mock_client.get_collections.return_value = MagicMock(collections=[])
    return mock_client


# ── In-memory mock database (replaces PostgreSQL for local testing) ─────


class _MockDatabase:
    """In-memory PostgreSQL mock supporting SQL patterns used by FLAI tests.

    Stores data in dicts/lists per table. The ``get_connection()`` method
    returns a mock connection whose cursor delegates to :meth:`_execute`.
    """

    def __init__(self):
        # Tables indexed by login / session-id
        self._users: dict[str, dict] = {}
        self._sessions: dict[str, dict] = {}
        self._messages: list[dict] = []
        self._model_configs: list[dict] = []
        self._documents: list[dict] = []
        self._document_folders: list[dict] = []
        self._storage: dict[str, dict] = {}
        self._visits: dict[tuple, dict] = {}
        self._user_sessions: dict[str, dict] = {}
        self._api_tokens: list[dict] = []
        self._branding: dict = {
            "id": 1,
            "logo_path": None,
            "logo_updated_at": None,
            "site_name_ru": "",
            "site_name_en": "",
            "updated_at": None,
        }
        self._next_user_id = 1
        self._next_msg_id = 1
        self._next_api_token_id = 1

        # State updated by _execute and read by fetchone / fetchall
        self._fetched: Any = None
        self._rowcount: int = -1
        self._lastrowid: Any = None

    # ── Public API for patching ────────────────────────────────────────

    def get_connection(self):
        """Return a mock psycopg2 connection that talks to this in-memory store."""
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = self._execute
        cursor.fetchone = MagicMock(side_effect=lambda: self._fetched)
        cursor.fetchall = MagicMock(
            side_effect=lambda: (
                self._fetched
                if isinstance(self._fetched, list)
                else [self._fetched]
                if self._fetched is not None
                else []
            )
        )
        type(cursor).rowcount = property(lambda s: self._rowcount)
        type(cursor).lastrowid = property(lambda s: self._lastrowid)
        conn.cursor.return_value = cursor
        conn.commit.return_value = None
        conn.close.return_value = None
        conn.rollback.return_value = None
        return conn

    # ── SQL dispatcher ─────────────────────────────────────────────────

    def _execute(self, sql: str, params: Any = None):
        if params is None:
            params = ()
        if not isinstance(params, (list, tuple)):
            params = (params,)

        sql_u = sql.strip().upper()

        # DDL – ignore
        if "CREATE TABLE" in sql_u or "CREATE INDEX" in sql_u:
            self._result(None)
            return

        if sql_u.startswith("SELECT"):
            self._do_select(sql, sql_u, params)
        elif sql_u.startswith("INSERT"):
            self._do_insert(sql, sql_u, params)
        elif sql_u.startswith("UPDATE"):
            self._do_update(sql, sql_u, params)
        elif sql_u.startswith("DELETE"):
            self._do_delete(sql, sql_u, params)
        else:
            self._result(None)

    def _result(self, value: Any, rowcount: int = -1):
        self._fetched = value
        self._rowcount = rowcount

    # ── SELECT ─────────────────────────────────────────────────────────

    def _do_select(self, sql: str, sql_u: str, params: tuple):
        # COUNT(*) FROM model_configs (only match top-level, not subqueries)
        top_sql = sql.strip()
        if top_sql.upper().startswith("SELECT COUNT(*)"):
            if "MODEL_CONFIGS" in sql_u:
                self._result({"cnt": len(self._model_configs)}, rowcount=1)
                return
            if "MESSAGES" in sql_u:
                session_id = params[0] if params else None
                cnt = sum(1 for m in self._messages if m.get("session_id") == session_id)
                self._result({"cnt": cnt}, rowcount=1)
                return
            if "CHAT_SESSIONS" in sql_u:
                uid = params[-1] if params else None
                cnt = sum(1 for s in self._sessions.values() if uid and s.get("user_id") == uid)
                self._result({"cnt": cnt}, rowcount=1)
                return

        # FROM model_configs (single lookup by module)
        if "FROM MODEL_CONFIGS" in sql_u and "WHERE" in sql_u:
            module = params[0] if params else None
            for cfg in self._model_configs:
                if cfg.get("module") == module:
                    self._result(dict(cfg), rowcount=1)
                    return
            self._result(None, rowcount=0)
            return

        # FROM users
        if "FROM USERS" in sql_u:
            if "ORDER BY LOGIN" in sql_u:
                users = [dict(u) for u in self._users.values()]
                if "LOGIN !=" in sql_u or "login !=" in sql:
                    users = [u for u in users if u.get("login") != "admin"]
                users.sort(key=lambda u: u.get("login", ""))
                self._result(users, rowcount=len(users))
                return
            if "WHERE LOGIN" in sql_u or "WHERE login" in sql:
                login = params[0] if params else None
                user = self._users.get(login)
                self._result(dict(user) if user else None, rowcount=1 if user else 0)
                return
            self._result(list(self._users.values()), rowcount=len(self._users))
            return

        # FROM chat_sessions
        if "FROM CHAT_SESSIONS" in sql_u:
            if "COUNT(DISTINCT CS.ID)" in sql_u and "COUNT(M.ID)" in sql_u:
                user_id = params[-1] if params else None
                user_sessions = [s for s in self._sessions.values() if s.get("user_id") == user_id]
                session_ids = {s["id"] for s in user_sessions}
                user_messages = [m for m in self._messages if m.get("session_id") in session_ids]
                active_keys = sum(1 for t in self._api_tokens if t["login"] == user_id and t["revoked_at"] is None)
                self._result(
                    {
                        "sessions": len(user_sessions),
                        "messages": len(user_messages),
                        "outgoing_tokens": sum(int(m.get("completion_tokens") or 0) for m in user_messages),
                        "incoming_tokens": sum(int(m.get("prompt_tokens") or 0) for m in user_messages),
                        "documents_count": 0,
                        "files_count": len({m.get("file_path") for m in user_messages if m.get("file_path")}),
                        "api_keys_count": active_keys,
                    },
                    rowcount=1,
                )
                return
            # Determine filter type from WHERE clause
            if "WHERE ID" in sql_u or "WHERE id" in sql:
                sid = params[0] if params else None
                s = self._sessions.get(sid)
                if s:
                    # Return just the requested columns
                    req = {"id": s["id"], "user_id": s.get("user_id", "")}
                    if "USER_ID" in sql_u:
                        self._result(req, rowcount=1)
                    else:
                        msg_count = len([m for m in self._messages if m.get("session_id") == s["id"]])
                        sess_msgs = [m for m in self._messages if m.get("session_id") == s["id"]]
                        total_prompt = sum(int(m.get("prompt_tokens") or 0) for m in sess_msgs)
                        total_completion = sum(int(m.get("completion_tokens") or 0) for m in sess_msgs)
                        self._result(
                            {
                                "id": s["id"],
                                "title": s.get("title", ""),
                                "user_id": s.get("user_id", ""),
                                "model_name": s.get("model_name", "auto"),
                                "created_at": s.get("created_at"),
                                "updated_at": s.get("updated_at"),
                                "last_visit": s.get("updated_at"),
                                "unread_count": 0,
                                "message_count": msg_count,
                                "total_prompt_tokens": total_prompt,
                                "total_completion_tokens": total_completion,
                            },
                            rowcount=1,
                        )
                else:
                    self._result(None, rowcount=0)
                return
            # Filter by user_id
            user_id = None
            for p in params:
                if isinstance(p, str):
                    user_id = p
                    break
            sessions = []
            for s in self._sessions.values():
                if user_id and s.get("user_id") != user_id:
                    continue
                msg_count = len([m for m in self._messages if m.get("session_id") == s["id"]])
                sess_msgs = [m for m in self._messages if m.get("session_id") == s["id"]]
                total_prompt = sum(int(m.get("prompt_tokens") or 0) for m in sess_msgs)
                total_completion = sum(int(m.get("completion_tokens") or 0) for m in sess_msgs)
                sessions.append(
                    {
                        "id": s["id"],
                        "title": s.get("title", ""),
                        "user_id": s.get("user_id", ""),
                        "model_name": s.get("model_name", "auto"),
                        "created_at": s.get("created_at"),
                        "updated_at": s.get("updated_at"),
                        "last_visit": s.get("updated_at"),
                        "unread_count": 0,
                        "message_count": msg_count,
                        "total_prompt_tokens": total_prompt,
                        "total_completion_tokens": total_completion,
                    }
                )
            # Support LIMIT for queries like: SELECT id FROM chat_sessions ... LIMIT 1
            limit = None
            for p in params:
                if isinstance(p, int):
                    limit = p
                    break
            sessions.sort(key=lambda s: str(s.get("updated_at", "")), reverse=True)
            if limit:
                sessions = sessions[:limit]
            self._result(sessions, rowcount=len(sessions))
            return

        # FROM messages
        if "FROM MESSAGES" in sql_u:
            if "LIKE" in sql_u:
                # Column-wide scans (e.g. the backfill migration) return all rows
                self._result(list(self._messages), rowcount=len(self._messages))
                return
            session_id = params[0]
            msgs = [dict(m) for m in self._messages if m.get("session_id") == session_id]
            # Only filter by file_path when it's a WHERE clause, not a CASE WHEN expression
            if "WHERE" in sql_u and "FILE_PATH IS NOT NULL" in sql_u and "CASE" not in sql_u:
                msgs = [m for m in msgs if m.get("file_path")]
            msgs.sort(key=lambda m: (str(m.get("timestamp", "")), m.get("id", 0)))
            # Extract LIMIT / OFFSET from params if present (they are ints)
            limit = 100
            offset = 0
            int_params = [p for p in params if isinstance(p, int)]
            if len(int_params) >= 2:
                limit, offset = int_params[0], int_params[1]
            elif len(int_params) == 1:
                limit = int_params[0]
            msgs = msgs[offset : offset + limit]
            self._result(msgs, rowcount=len(msgs))
            return

        # FROM document_folders
        if "FROM DOCUMENT_FOLDERS" in sql_u:
            if len(params) > 1:
                fid = params[0]
                uid = params[-1]
                folders = [dict(f) for f in self._document_folders if f.get("id") == fid and f.get("user_id") == uid]
                self._result(folders[0] if folders else None, rowcount=len(folders))
                return
            uid = params[0] if params else None
            folders = [dict(f) for f in self._document_folders if f.get("user_id") == uid]
            folders.sort(key=lambda f: (str(f.get("created_at", "")), str(f.get("name", ""))))
            self._result(folders, rowcount=len(folders))
            return

        # FROM documents
        if "FROM DOCUMENTS" in sql_u:
            if "WHERE USER_ID" in sql_u or "WHERE user_id" in sql:
                user_id = params[0]
                docs = [dict(d) for d in self._documents if d.get("user_id") == user_id]
                if "FOLDER_ID" in sql_u and len(params) > 1:
                    folder_id = params[1]
                    docs = [d for d in docs if d.get("folder_id") == folder_id]
                docs.sort(key=lambda d: str(d.get("uploaded_at", "")), reverse=True)
                self._result(docs, rowcount=len(docs))
                return
            if "WHERE ID" in sql_u or "WHERE id" in sql:
                doc_id = params[0]
                uid = params[1] if len(params) > 1 else None
                for d in self._documents:
                    if d.get("id") == doc_id and (uid is None or d.get("user_id") == uid):
                        self._result(dict(d), rowcount=1)
                        return
                self._result(None, rowcount=0)
                return
            self._result(list(self._documents), rowcount=len(self._documents))
            return

        # FROM user_sessions
        if "FROM USER_SESSIONS" in sql_u:
            uid = params[0] if params else None
            rec = self._user_sessions.get(uid) if uid else None
            self._result(dict(rec) if rec else None, rowcount=1 if rec else 0)
            return

        # FROM user_storage
        if "FROM USER_STORAGE" in sql_u:
            user_id = params[0] if params else None
            data = self._storage.get(user_id) if user_id else None
            self._result(dict(data) if data else None, rowcount=1 if data else 0)
            return

        if "FROM API_TOKENS" in sql_u:
            rows = list(self._api_tokens)
            if "REVOKED_AT IS NULL" in sql_u:
                rows = [row for row in rows if row["revoked_at"] is None]
            if "TOKEN_HASH = %S" in sql_u:
                rows = [row for row in rows if row["token_hash"] == params[0]]
            if "LOGIN = %S" in sql_u:
                rows = [row for row in rows if row["login"] == params[0]]
            selected_columns = sql[sql_u.index("SELECT") + len("SELECT") : sql_u.index("FROM")]
            columns = [column.strip().split()[-1].strip('"') for column in selected_columns.split(",")]
            rows = [{column: row[column] for column in columns if column in row} for row in rows]
            if "ORDER BY" in sql_u:
                rows.sort(key=lambda row: (row.get("created_at") or "", row.get("id", 0)), reverse=True)
                self._result([dict(row) for row in rows], rowcount=len(rows))
            else:
                self._result(dict(rows[0]) if rows else None, rowcount=1 if rows else 0)
            return

        # FROM branding_settings (singleton branding row)
        if "FROM BRANDING_SETTINGS" in sql_u:
            self._result(dict(self._branding), rowcount=1)
            return

        self._result(None, rowcount=0)

    # ── INSERT ─────────────────────────────────────────────────────────

    def _do_insert(self, sql: str, sql_u: str, params: tuple):
        if "INTO API_TOKENS" in sql_u:
            token_id = self._next_api_token_id
            self._next_api_token_id += 1
            row = {
                "id": token_id,
                "login": params[0],
                "name": params[1],
                "token_hash": params[2],
                "token_prefix": params[3],
                "created_at": None,
                "last_used_at": None,
                "revoked_at": None,
            }
            self._api_tokens.append(row)
            self._result({"id": token_id, "created_at": None}, rowcount=1)
            return

        # INTO users
        if "INTO USERS" in sql_u:
            login = params[0]
            user = {
                "id": str(self._next_user_id),
                "login": login,
                "name": params[1],
                "password_hash": params[2],
                "service_class": params[3],
                "is_admin": params[4],
                "is_active": True,
                "camera_permissions": params[5] if len(params) > 5 else None,
                "language": params[6] if len(params) > 6 else "ru",
                "voice_gender": params[7] if len(params) > 7 else "male",
                "theme": params[8] if len(params) > 8 else "light",
                "created_at": None,
                "updated_at": None,
            }
            self._next_user_id += 1
            self._users[login] = user
            self._result(None, rowcount=1)
            return

        # INTO model_configs (seed from init_db)
        if "INTO MODEL_CONFIGS" in sql_u:
            reasoning_model = params[0] if params else "gpt-oss-20b-mxfp4"
            defaults = [
                {
                    "module": "multimodal",
                    "model_name": "Qwen3VL-8B-Instruct-Q4_K_M",
                    "context_length": 16384,
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "timeout": 120,
                    "service_url": "http://flai-llamacpp:8033",
                    "repeat_penalty": 1.1,
                },
                {
                    "module": "reasoning",
                    "model_name": reasoning_model,
                    "context_length": 16384,
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "timeout": 120,
                    "service_url": "http://flai-llamacpp:8033",
                    "repeat_penalty": 1.15,
                },
                {
                    "module": "embedding",
                    "model_name": "bge-m3-Q8_0",
                    "context_length": 512,
                    "temperature": None,
                    "top_p": None,
                    "timeout": 120,
                    "service_url": "http://flai-llamacpp:8033",
                    "repeat_penalty": None,
                },
            ]
            self._model_configs = defaults
            self._result(None, rowcount=len(defaults))
            return

        # INTO chat_sessions
        if "INTO CHAT_SESSIONS" in sql_u:
            sid = params[0]
            self._sessions[sid] = {
                "id": sid,
                "user_id": params[1],
                "title": params[2],
                "model_name": params[3] if len(params) > 3 else "auto",
                "created_at": params[4] if len(params) > 4 else None,
                "updated_at": params[5] if len(params) > 5 else None,
            }
            self._result(None, rowcount=1)
            return

        # INTO session_visits
        if "INTO SESSION_VISITS" in sql_u:
            uid, sid, visit = params[0], params[1], params[2]
            key = (uid, sid)
            self._visits[key] = {"user_id": uid, "session_id": sid, "last_visit": visit}
            self._result(None, rowcount=1)
            return

        # INTO messages
        if "INTO MESSAGES" in sql_u:
            msg_id = self._next_msg_id
            self._next_msg_id += 1
            msg = {
                "id": msg_id,
                "session_id": params[0],
                "role": params[1],
                "content": params[2],
                "file_data": params[3],
                "file_type": params[4],
                "file_name": params[5],
                "file_path": params[6],
                "model_name": params[7],
                "timestamp": params[8],
                "response_time": params[9] if len(params) > 9 else None,
                "mm_time": params[10] if len(params) > 10 else None,
                "gen_time": params[11] if len(params) > 11 else None,
                "mm_model": params[12] if len(params) > 12 else None,
                "gen_model": params[13] if len(params) > 13 else None,
                "response_style": params[14] if len(params) > 14 else None,
                "completion_tokens": params[15] if len(params) > 15 else None,
                "model_type": params[16] if len(params) > 16 else None,
                "prompt_tokens": params[17] if len(params) > 17 else None,
            }
            self._messages.append(msg)
            self._lastrowid = msg_id
            self._result({"id": msg_id}, rowcount=1)
            return

        # INTO documents
        if "INTO DOCUMENTS" in sql_u:
            doc = {
                "id": params[0],
                "user_id": params[1],
                "filename": params[2],
                "file_size": params[3],
                "file_ext": params[4],
                "file_path": params[5],
                "uploaded_at": params[6] if len(params) > 6 else None,
                "index_status": None,
                "indexed_at": None,
                "indexing_started_at": None,
                "embedding_model": None,
                "description_model": None,
                "folder_id": params[7] if len(params) > 7 else None,
            }
            self._documents.append(doc)
            self._result(None, rowcount=1)
            return

        # INTO document_folders
        if "INTO DOCUMENT_FOLDERS" in sql_u:
            self._document_folders.append(
                {
                    "id": params[0],
                    "user_id": params[1],
                    "name": params[2],
                    "created_at": params[3],
                }
            )
            self._result(None, rowcount=1)
            return

        # INTO user_storage
        if "INTO USER_STORAGE" in sql_u:
            uid = params[0]
            used = max(0, params[1]) if len(params) > 1 else 0
            self._storage[uid] = {"user_id": uid, "used_bytes": used}
            self._result(None, rowcount=1)
            return

        # INTO user_sessions
        if "INTO USER_SESSIONS" in sql_u:
            uid, sid = params[0], params[1]
            self._user_sessions[uid] = {"user_id": uid, "last_session_id": sid}
            self._result(None, rowcount=1)
            return

        self._result(None, rowcount=0)

    # ── UPDATE ─────────────────────────────────────────────────────────

    def _do_update(self, sql: str, sql_u: str, params: tuple):
        if "API_TOKENS" in sql_u:
            token_id = params[0]
            login = params[1] if len(params) > 1 else None
            for row in self._api_tokens:
                if row["id"] != token_id or (login is not None and row["login"] != login):
                    continue
                if "SET REVOKED_AT = CURRENT_TIMESTAMP" in sql_u:
                    row["revoked_at"] = "now"
                if "LAST_USED_AT = CURRENT_TIMESTAMP" in sql_u:
                    row["last_used_at"] = "now"
                self._result(None, rowcount=1)
                return
            self._result(None, rowcount=0)
            return

        # UPDATE user_sessions (must come BEFORE the USERS check to avoid substring match)
        if "USER_SESSIONS" in sql_u:
            uid = params[-1]
            if uid in self._user_sessions:
                set_clause = sql[sql.index("SET") + 3 :]
                if "WHERE" in set_clause.upper():
                    set_clause = set_clause[: set_clause.upper().index("WHERE")]
                fields = re.findall(r"(\w+)\s*=\s*%s", set_clause, re.IGNORECASE)
                if fields:
                    for i, field in enumerate(fields):
                        if i < len(params) - 1:
                            self._user_sessions[uid][field] = params[i]
            self._result(None, rowcount=1 if uid in self._user_sessions else 0)
            return
        # UPDATE users
        if "USERS" in sql_u:
            login = params[-1]  # WHERE login = %s is always last
            user = self._users.get(login)
            if user:
                set_clause = sql[sql.index("SET") + 3 :]
                if "WHERE" in set_clause.upper():
                    set_clause = set_clause[: set_clause.upper().index("WHERE")]
                fields = re.findall(r"(\w+)\s*=\s*%s", set_clause, re.IGNORECASE)
                if fields:
                    for i, field in enumerate(fields):
                        if i < len(params) - 1:
                            user[field] = params[i]
            self._result(None, rowcount=1 if user else 0)
            return

        # UPDATE chat_sessions
        if "CHAT_SESSIONS" in sql_u:
            sid = params[-1]
            session = self._sessions.get(sid)
            if session:
                set_clause = sql[sql.index("SET") + 3 :]
                if "WHERE" in set_clause.upper():
                    set_clause = set_clause[: set_clause.upper().index("WHERE")]
                fields = re.findall(r"(\w+)\s*=\s*%s", set_clause, re.IGNORECASE)
                if fields:
                    for i, field in enumerate(fields):
                        if i < len(params) - 1:
                            session[field] = params[i]
            self._result(None, rowcount=1 if session else 0)
            return

        # UPDATE document_folders
        if "DOCUMENT_FOLDERS" in sql_u:
            fid = params[-2] if len(params) >= 2 else None
            uid = params[-1]
            folder = next(
                (f for f in self._document_folders if f.get("id") == fid and f.get("user_id") == uid),
                None,
            )
            if folder:
                set_clause = sql[sql.index("SET") + 3 :]
                if "WHERE" in set_clause.upper():
                    set_clause = set_clause[: set_clause.upper().index("WHERE")]
                fields = re.findall(r"(\w+)\s*=\s*%s", set_clause, re.IGNORECASE)
                for i, field in enumerate(fields):
                    if i < len(params) - 2:
                        folder[field] = params[i]
            self._result(None, rowcount=1 if folder else 0)
            return

        # UPDATE documents (folder)
        if "SET FOLDER_ID" in sql_u:
            folder_id, doc_id, uid = params[-3], params[-2], params[-1]
            doc = next(
                (d for d in self._documents if d.get("id") == doc_id and d.get("user_id") == uid),
                None,
            )
            if doc:
                doc["folder_id"] = folder_id
            self._result(None, rowcount=1 if doc else 0)
            return

        # UPDATE documents
        if "DOCUMENTS" in sql_u:
            doc_id = params[-1]
            for doc in self._documents:
                if doc.get("id") == doc_id:
                    set_clause = sql[sql.index("SET") + 3 :]
                    if "WHERE" in set_clause.upper():
                        set_clause = set_clause[: set_clause.upper().index("WHERE")]
                    fields = re.findall(r"(\w+)\s*=\s*%s", set_clause, re.IGNORECASE)
                    if fields:
                        for i, field in enumerate(fields):
                            if i < len(params) - 1:
                                doc[field] = params[i]
                    break
            self._result(None, rowcount=1)
            return

        # UPDATE branding_settings (singleton row)
        if "BRANDING_SETTINGS" in sql_u:
            set_clause = sql[sql.index("SET") + 3 :]
            if "WHERE" in set_clause.upper():
                set_clause = set_clause[: set_clause.upper().index("WHERE")]
            fields = re.findall(r"(\w+)\s*=\s*%s", set_clause, re.IGNORECASE)
            for i, field in enumerate(fields):
                if i < len(params):
                    self._branding[field] = params[i]
            self._result(None, rowcount=1)
            return

        # UPDATE messages (per-row and column-scan bulk updates)
        if "UPDATE" in sql_u and "MESSAGES" in sql_u:
            set_clause = sql[sql.index("SET") + 3 :]
            if "WHERE" in set_clause.upper():
                set_clause = set_clause[: set_clause.upper().index("WHERE")]
            fields = re.findall(r"(\w+)\s*=\s*%s", set_clause, re.IGNORECASE)
            if params:
                targets = [m for m in self._messages if m.get("id") == params[-1]]
                for m in targets:
                    for i, field in enumerate(fields):
                        if i < len(params) - 1:
                            m[field] = params[i]
                self._result(None, rowcount=len(targets))
                return
            # Bulk without bind values (e.g. "SET file_data = NULL WHERE ...")
            if "FILE_DATA = NULL" in sql_u:
                for m in self._messages:
                    if m.get("file_path"):
                        m["file_data"] = None
                self._result(None, rowcount=1)
                return
            self._result(None, rowcount=0)
            return

        self._result(None, rowcount=0)

    # ── DELETE ─────────────────────────────────────────────────────────

    def _do_delete(self, sql: str, sql_u: str, params: tuple):
        # DELETE FROM messages WHERE session_id = %s
        if "FROM MESSAGES" in sql_u:
            sid = params[0]
            self._messages = [m for m in self._messages if m.get("session_id") != sid]
            self._result(None, rowcount=1)
            return

        # DELETE FROM messages USING ... (cascade from delete_user)
        if "MESSAGES M" in sql_u or "USING MESSAGES" in sql_u:
            uid = params[0]
            sids = {s["id"] for s in self._sessions.values() if s.get("user_id") == uid}
            self._messages = [m for m in self._messages if m.get("session_id") not in sids]
            self._result(None, rowcount=len(sids) if sids else 1)
            return

        # DELETE FROM chat_sessions
        if "FROM CHAT_SESSIONS" in sql_u:
            sid = params[0]
            self._sessions.pop(sid, None)
            self._result(None, rowcount=1)
            return

        # DELETE FROM users
        if "FROM USERS" in sql_u:
            login = params[0]
            self._users.pop(login, None)
            self._result(None, rowcount=1)
            return

        # DELETE FROM document_folders
        if "FROM DOCUMENT_FOLDERS" in sql_u:
            if "AND" in sql_u:
                fid = params[0]
                uid = params[1] if len(params) > 1 else None
                prev = len(self._document_folders)
                self._document_folders = [
                    f for f in self._document_folders if not (f.get("id") == fid and f.get("user_id") == uid)
                ]
                self._result(None, rowcount=0 if len(self._document_folders) == prev else 1)
                return
            uid = params[0]
            self._document_folders = [f for f in self._document_folders if f.get("user_id") != uid]
            self._result(None, rowcount=1)
            return

        # DELETE FROM documents
        if "FROM DOCUMENTS" in sql_u:
            if "WHERE USER_ID" in sql_u or "WHERE user_id" in sql:
                uid = params[0]
                self._documents = [d for d in self._documents if d.get("user_id") != uid]
            else:
                doc_id = params[0]
                self._documents = [d for d in self._documents if d.get("id") != doc_id]
            self._result(None, rowcount=1)
            return

        # DELETE FROM session_visits
        if "FROM SESSION_VISITS" in sql_u:
            pid = params[0]
            if "WHERE USER_ID" in sql_u or "WHERE user_id" in sql:
                self._visits = {k: v for k, v in self._visits.items() if v.get("user_id") != pid}
            else:
                self._visits = {k: v for k, v in self._visits.items() if v.get("session_id") != pid}
            self._result(None, rowcount=1)
            return

        # DELETE FROM user_sessions
        if "FROM USER_SESSIONS" in sql_u:
            uid = params[0]
            self._user_sessions.pop(uid, None)
            self._result(None, rowcount=1)
            return

        # DELETE FROM user_storage
        if "FROM USER_STORAGE" in sql_u:
            uid = params[0]
            self._storage.pop(uid, None)
            self._result(None, rowcount=1)
            return

        self._result(None, rowcount=0)


class FakeRedis:
    """In-memory Redis double for the API task registry Lua script."""

    def __init__(self):
        self.hashes = {}
        self.sorted_sets = {}
        self.expirations = {}

    def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)

    def hsetnx(self, key, field, value):
        values = self.hashes.setdefault(key, {})
        if field in values:
            return False
        values[field] = value
        return True

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def zadd(self, key, mapping):
        self.sorted_sets.setdefault(key, {}).update(mapping)

    def zrevrange(self, key, start, stop, withscores=False):
        values = sorted(self.sorted_sets.get(key, {}).items(), key=lambda pair: pair[1], reverse=True)
        selected = values[start : stop + 1]
        return selected if withscores else [member for member, _score in selected]

    def zcard(self, key):
        return len(self.sorted_sets.get(key, {}))

    def zremrangebyrank(self, key, start, stop):
        values = self.sorted_sets.get(key, {})
        ordered = sorted(values, key=values.get)
        stop = len(ordered) + stop if stop < 0 else stop
        removed = ordered[start : stop + 1]
        for member in removed:
            values.pop(member, None)
        return len(removed)

    def eval(
        self,
        script,
        key_count,
        task_key,
        index_key,
        login,
        session_id,
        endpoint,
        created_at,
        ttl,
        task_id,
        maximum,
        only_if_absent,
    ):
        existing = self.hashes.get(task_key, {})
        if only_if_absent == "1" and existing:
            required = ("login", "session_id", "endpoint", "created_at")
            if any(key not in existing for key in required):
                return 0
            if (existing["login"], existing["session_id"], existing["endpoint"]) != (login, session_id, endpoint):
                return 0
            created_at = existing["created_at"]
        else:
            self.hashes[task_key] = {
                "login": login,
                "session_id": session_id,
                "endpoint": endpoint,
                "created_at": str(created_at),
            }
        self.zadd(index_key, {task_id: float(created_at)})
        if self.zcard(index_key) > int(maximum):
            self.zremrangebyrank(index_key, 0, -int(maximum) - 1)
        self.expire(task_key, int(ttl))
        self.expire(index_key, int(ttl))
        return 1

    def expire(self, key, seconds):
        self.expirations[key] = seconds

    def hdel(self, key, field):
        return self.hashes.get(key, {}).pop(field, None) is not None

    def close(self):
        return None


@pytest.fixture
def task_redis(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr("app.api_bridge.get_redis_client", lambda: redis)
    return redis


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(scope="function")
def test_app():
    """
    Create Flask app with isolated temp directories and mocked services.

    Database behaviour depends on ``_USE_MOCK_DB``:
      * ``True``  – an in-memory ``_MockDatabase`` replaces PostgreSQL
      * ``False`` – real PostgreSQL is used (CI with service container)
    """
    temp_dir = tempfile.mkdtemp()

    # Env vars that tests expect – set before create_app()
    os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"
    os.environ["REDIS_URL"] = "redis://localhost:6379/0"
    os.environ["LLAMACPP_URL"] = "http://localhost:8080"
    os.environ["LLAMA_SWAP_URL"] = ""
    os.environ["WHISPER_API_URL"] = ""
    os.environ["SD_WRAPPER_URL"] = ""
    os.environ["PIPER_URL"] = ""
    os.environ["CAMERA_ENABLED"] = "false"
    os.environ["QDRANT_URL"] = "http://localhost:6333"
    os.environ["LOG_LEVEL"] = "CRITICAL"
    os.environ["SERVICE_RETRY_ATTEMPTS"] = "1"  # silence app logs

    mock_redis = create_mock_redis()
    mock_llamacpp = create_mock_llamacpp()
    mock_qdrant = create_mock_qdrant()

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch("redis.from_url", return_value=mock_redis))
        stack.enter_context(patch("app.llamacpp_client.LlamaCppClient", return_value=mock_llamacpp))
        stack.enter_context(patch("modules.rag.QdrantClient", return_value=mock_qdrant))

        if _USE_MOCK_DB:
            mock_db = _MockDatabase()
            stack.enter_context(patch("app.database.get_db_connection", return_value=mock_db.get_connection()))

        flask_app = create_app()

        flask_app.config.update(
            {
                "TESTING": True,
                "WTF_CSRF_ENABLED": False,
                "RATELIMIT_ENABLED": False,
                "MAX_DOCUMENT_SIZE_MB": 5,
                "UPLOAD_FOLDER": os.path.join(temp_dir, "uploads"),
                "DOCUMENTS_FOLDER": os.path.join(temp_dir, "documents"),
                # A test that forgets to stub the bridge would otherwise poll
                # the mock Redis in a hot loop for the production 600s budget.
                # Tests that exercise the 408 path set their own value.
                "API_SYNC_MAX_WAIT": 5,
            }
        )
        os.makedirs(flask_app.config["UPLOAD_FOLDER"], exist_ok=True)
        os.makedirs(flask_app.config["DOCUMENTS_FOLDER"], exist_ok=True)

        yield flask_app

        # Teardown: stop background Redis worker threads
        if hasattr(flask_app, "request_queue"):
            flask_app.request_queue.stop_workers(timeout=3)

        # Teardown: stop the per-app watcher threads (see helper above)
        stop_app_background_threads(flask_app)

        # Teardown: clean real DB between tests to prevent cross-test pollution
        if not _USE_MOCK_DB:
            try:
                from app.database import get_db

                with get_db() as conn:
                    c = conn.cursor()
                    c.execute(
                        "TRUNCATE TABLE api_tokens, branding_settings, camera_rooms, "
                        "chat_sessions, document_folders, documents, gguf_models_cache, "
                        "messages, model_configs, model_vram_estimates, session_visits, "
                        "slm_import_progress, user_sessions, user_storage, users "
                        "RESTART IDENTITY CASCADE"
                    )
            except Exception:
                pass

    with contextlib.suppress(Exception):
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def client(test_app):
    """Flask test client for making HTTP requests."""
    return test_app.test_client()


@pytest.fixture
def runner(test_app):
    """CLI runner for testing Flask commands."""
    return test_app.test_cli_runner()


@pytest.fixture
def mock_redis_client():
    with patch("redis.from_url") as mock_redis:
        mock_redis.return_value = create_mock_redis()
        yield mock_redis.return_value


@pytest.fixture
def mock_llamacpp_client():
    with patch("app.llamacpp_client.LlamaCppClient") as mock_llamacpp:
        mock_llamacpp.return_value = create_mock_llamacpp()
        yield mock_llamacpp.return_value


@pytest.fixture
def mock_qdrant_client():
    with patch("modules.rag.QdrantClient") as mock_qdrant:
        mock_qdrant.return_value = create_mock_qdrant()
        yield mock_qdrant.return_value


# Alias for pytest-flask plugin compatibility
@pytest.fixture(scope="function")
def app(test_app):
    """Alias for test_app to support pytest-flask plugin."""
    return test_app
