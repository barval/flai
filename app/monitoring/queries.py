"""Aggregation of the monitoring series: live rows plus the deletion archive.

Every series is one bounded query per source. Buckets are computed in UTC from
timestamps that are normalised in SQL — naive columns hold local time and are
converted with ``AT TIME ZONE``, ``TIMESTAMPTZ`` columns are used as-is.
"""

from datetime import UTC, datetime

from flask import current_app

from app.database import get_db
from app.monitoring.archive import PLAN_IN_SQL, PLAN_OUT_SQL, TASK_STEP_IN_SQL, TASK_STEP_OUT_SQL
from app.monitoring.ranges import RANGE_SPECS, bucket_starts
from app.monitoring.snapshots import storage_series
from app.monitoring.timez import app_timezone

SERIES_KEYS = (
    "sessions",
    "messages",
    "tasks",
    "documents",
    "files",
    "outgoing_tokens",
    "incoming_tokens",
    "web_requests",
    "storage_bytes",
)

_ARCHIVE_COUNT_KINDS = {
    "session": "sessions",
    "message": "messages",
    "task": "tasks",
    "document": "documents",
}
_TOKEN_KINDS = ("message", "task_step", "task_plan")


def _bucket_expr(ts_sql: str, seconds: int) -> str:
    """``to_timestamp`` expression that floors a timestamp onto a UTC bucket."""
    return f"to_timestamp(floor(extract(epoch FROM {ts_sql}) / {seconds}) * {seconds})"


def _ts(column: str, naive: bool) -> str:
    """Timestamp expression: naive columns are local time, so conversion is explicit."""
    return f"({column} AT TIME ZONE %(tz)s::text)" if naive else column


def _params(logins: list[str], since: datetime, until: datetime, tz: str) -> dict:
    return {"logins": list(logins), "since": since, "until": until, "tz": tz}


def _fetch(cursor, sql: str, params: dict) -> list[dict]:
    cursor.execute(sql, params)
    return cursor.fetchall()


def build_payload(logins: list[str], range_key: str, now: datetime | None = None) -> dict:
    """Return every series for the selected users and range."""
    _window, bucket = RANGE_SPECS[range_key]
    size = int(bucket.total_seconds())
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    starts = bucket_starts(moment, range_key)
    since = starts[0]
    until = starts[-1] + bucket
    tz = app_timezone()

    series: dict[str, list] = {key: [0] * len(starts) for key in SERIES_KEYS}
    series["storage_bytes"] = [None] * len(starts)
    positions = {start: position for position, start in enumerate(starts)}
    current_bytes = 0
    storage_since = None
    storage_overrides: dict[str, int] = {}

    if logins:
        params = _params(logins, since, until, tz)
        with get_db() as conn:
            cursor = conn.cursor()

            session_ts = _ts("created_at", naive=True)
            session_rows = _fetch(
                cursor,
                f"""
                SELECT {_bucket_expr(session_ts, size)} AS bucket, COUNT(*) AS n
                FROM chat_sessions
                WHERE user_id = ANY(%(logins)s)
                  AND {session_ts} >= %(since)s AND {session_ts} < %(until)s
                GROUP BY bucket
                """,
                params,
            )

            message_ts = _ts("m.timestamp", naive=True)
            message_rows = _fetch(
                cursor,
                f"""
                SELECT {_bucket_expr(message_ts, size)} AS bucket,
                       COUNT(*) AS n,
                       COALESCE(SUM(m.prompt_tokens), 0) AS out_tokens,
                       COALESCE(SUM(m.completion_tokens), 0) AS in_tokens
                FROM messages m JOIN chat_sessions cs ON cs.id = m.session_id
                WHERE cs.user_id = ANY(%(logins)s)
                  AND {message_ts} >= %(since)s AND {message_ts} < %(until)s
                GROUP BY bucket
                """,
                params,
            )

            file_rows = _fetch(
                cursor,
                f"""
                SELECT {_bucket_expr(message_ts, size)} AS bucket,
                       COUNT(DISTINCT m.file_path) AS n
                FROM messages m JOIN chat_sessions cs ON cs.id = m.session_id
                WHERE cs.user_id = ANY(%(logins)s)
                  AND m.file_path IS NOT NULL AND m.file_path <> ''
                  AND {message_ts} >= %(since)s AND {message_ts} < %(until)s
                GROUP BY bucket
                """,
                params,
            )

            task_ts = _ts("created_at", naive=False)
            task_rows = _fetch(
                cursor,
                f"""
                SELECT {_bucket_expr(task_ts, size)} AS bucket, COUNT(*) AS n
                FROM tasks
                WHERE user_id = ANY(%(logins)s)
                  AND {task_ts} >= %(since)s AND {task_ts} < %(until)s
                GROUP BY bucket
                """,
                params,
            )

            document_ts = _ts("uploaded_at", naive=True)
            document_rows = _fetch(
                cursor,
                f"""
                SELECT {_bucket_expr(document_ts, size)} AS bucket, COUNT(*) AS n
                FROM documents
                WHERE user_id = ANY(%(logins)s)
                  AND {document_ts} >= %(since)s AND {document_ts} < %(until)s
                GROUP BY bucket
                """,
                params,
            )

            step_ts = _ts("ts.finished_at", naive=False)
            step_rows = _fetch(
                cursor,
                f"""
                SELECT {_bucket_expr(step_ts, size)} AS bucket,
                       COALESCE(SUM({TASK_STEP_OUT_SQL}), 0) AS out_tokens,
                       COALESCE(SUM({TASK_STEP_IN_SQL}), 0) AS in_tokens
                FROM task_steps ts JOIN tasks t ON t.id = ts.task_id
                WHERE t.user_id = ANY(%(logins)s)
                  AND {step_ts} >= %(since)s AND {step_ts} < %(until)s
                GROUP BY bucket
                """,
                params,
            )

            plan_rows = _fetch(
                cursor,
                f"""
                SELECT {_bucket_expr(task_ts, size)} AS bucket,
                       COALESCE(SUM({PLAN_OUT_SQL}), 0) AS out_tokens,
                       COALESCE(SUM({PLAN_IN_SQL}), 0) AS in_tokens
                FROM tasks t
                CROSS JOIN LATERAL (
                    SELECT t.step_plan->'plan_meta'->'usage' AS usage
                    UNION ALL
                    SELECT rev.value->'usage'
                    FROM jsonb_each(COALESCE(t.step_plan->'revision_meta', '{{}}'::jsonb)) AS rev
                ) u
                WHERE t.user_id = ANY(%(logins)s)
                  AND {task_ts} >= %(since)s AND {task_ts} < %(until)s
                GROUP BY bucket
                """,
                params,
            )

            web_rows = _fetch(
                cursor,
                f"""
                SELECT {_bucket_expr(_ts("occurred_at", naive=False), size)} AS bucket,
                       COALESCE(SUM(request_count), 0) AS n
                FROM web_requests
                WHERE user_id = ANY(%(logins)s)
                  AND occurred_at >= %(since)s AND occurred_at < %(until)s
                GROUP BY bucket
                """,
                params,
            )

            archive_rows = _fetch(
                cursor,
                f"""
                SELECT kind, {_bucket_expr(_ts("occurred_at", naive=False), size)} AS bucket,
                       COUNT(*) AS n,
                       COALESCE(SUM(out_tokens), 0) AS out_tokens,
                       COALESCE(SUM(in_tokens), 0) AS in_tokens,
                       COALESCE(SUM(file_count), 0) AS files
                FROM monitoring_archive
                WHERE user_id = ANY(%(logins)s)
                  AND occurred_at >= %(since)s AND occurred_at < %(until)s
                GROUP BY kind, bucket
                """,
                params,
            )

            cursor.execute(
                "SELECT COALESCE(SUM(used_bytes), 0) AS used FROM user_storage WHERE user_id = ANY(%s)",
                (list(logins),),
            )
            current_bytes = int(cursor.fetchone()["used"])

            cursor.execute(
                "SELECT user_id, limit_bytes FROM user_storage "
                "WHERE user_id = ANY(%s) AND limit_bytes IS NOT NULL",
                (list(logins),),
            )
            for row in cursor.fetchall():
                storage_overrides[row["user_id"]] = int(row["limit_bytes"])

        series["storage_bytes"], storage_since = storage_series(logins, starts, until)

        for row in session_rows:
            _add(series, positions, "sessions", row["bucket"], row["n"])
        for row in message_rows:
            position = positions.get(row["bucket"])
            if position is None:
                continue
            series["messages"][position] += int(row["n"])
            series["outgoing_tokens"][position] += int(row["out_tokens"])
            series["incoming_tokens"][position] += int(row["in_tokens"])
        for row in file_rows:
            _add(series, positions, "files", row["bucket"], row["n"])
        for row in task_rows:
            _add(series, positions, "tasks", row["bucket"], row["n"])
        for row in document_rows:
            _add(series, positions, "documents", row["bucket"], row["n"])
        for row in web_rows:
            _add(series, positions, "web_requests", row["bucket"], row["n"])
        for row in step_rows + plan_rows:
            position = positions.get(row["bucket"])
            if position is None:
                continue
            series["outgoing_tokens"][position] += int(row["out_tokens"])
            series["incoming_tokens"][position] += int(row["in_tokens"])
        for row in archive_rows:
            position = positions.get(row["bucket"])
            if position is None:
                continue
            kind = row["kind"]
            target = _ARCHIVE_COUNT_KINDS.get(kind)
            if target:
                series[target][position] += int(row["n"])
            if kind == "message":
                series["files"][position] += int(row["files"])
            if kind in _TOKEN_KINDS:
                series["outgoing_tokens"][position] += int(row["out_tokens"])
                series["incoming_tokens"][position] += int(row["in_tokens"])

    default_limit_bytes = int(current_app.config.get("MAX_UPLOAD_STORAGE_MB", 500)) * 1024 * 1024
    # Each user may carry an individual limit; "all users" sums the effective ones.
    limit_bytes = sum(storage_overrides.get(login, default_limit_bytes) for login in logins)
    if not logins:
        limit_bytes = default_limit_bytes

    # Host load is system-wide, not per user: it rides along with every payload.
    from app.monitoring.system import system_series

    host = system_series(starts, size)
    series.update(host["series"])

    return {
        "range": range_key,
        "bucket_seconds": size,
        "from": since.isoformat(),
        "to": until.isoformat(),
        "users": [{"login": login} for login in logins],
        "buckets": [start.isoformat() for start in starts],
        "series": series,
        "system": host["latest"],
        "totals": {key: int(sum(series[key])) for key in SERIES_KEYS if key != "storage_bytes"},
        "storage": {
            "current_bytes": current_bytes,
            "limit_bytes": limit_bytes,
            "since": storage_since.isoformat() if storage_since else None,
        },
    }


def _add(series: dict, positions: dict, key: str, bucket, value) -> None:
    position = positions.get(bucket)
    if position is not None:
        series[key][position] += int(value)
