# Monitoring and Health


## Health Check Endpoint
```bash
curl http://localhost:5000/health
```

**Response:**
```json
{
  "status": "ok",
  "timestamp": "2026-04-08T23:00:00.000000+00:00",
  "services": {
    "web": "ok",
    "database": "ok",
    "redis": "ok",
    "llamacpp": "ok",
    "qdrant": "ok",
    "sd_wrapper": "ok",
    "whisper": "ok",
    "ltx_video": "ok"
  }
}
```

## Prometheus Metrics
```bash
curl http://localhost:5000/metrics
```

---

## Admin Monitoring Tab

The admin panel's **Monitoring** tab (between **Users** and **Model Hub**) shows how the
instance is used over time — for one user or for all users — and never touches the GPU.

### What each chart counts

| Chart | Definition |
|---|---|
| Storage | `user_storage.used_bytes` per user, summed; the card shows "used / limit (percent)" and the bars show the hourly history |
| Sessions | `chat_sessions` rows created in the range |
| Messages | `messages` rows of the user's sessions |
| Tasks | `tasks` rows created in the range |
| Internet requests | Outbound calls to the internet from `web_requests`: every search (Tavily or SearXNG), every page downloaded for text extraction, and every page a crawl fetched — one row per call, summed by `request_count`. Only counts are stored (no queries, no URLs). Retained with the archive (45 days), erased with the account. |
| Outgoing tokens | Chat tokens (`messages.prompt_tokens`) + task step tokens (`task_steps.extra`, the same `*_total`-then-`usage` fallback the Users tab uses) + planner and revision tokens (`tasks.step_plan.plan_meta` / `revision_meta`) |
| Incoming tokens | The same three sources for `completion_tokens` |
| Files | Distinct `messages.file_path` values — chat attachments added in the range |
| Documents | Every `documents` row, exactly like the Documents panel (the Users tab counter was aligned to the same definition) |

A document attached in chat is written to both `messages.file_path` and `documents`, so it
counts in **Files** and in **Documents** by design. Storage is larger than both because it
also contains task media.

"All users" means every non-admin account — the same list the Users tab shows. Inactive users
are included.

### Ranges and buckets

| Range | Bucket |
|---|---|
| 1 hour | 5 minutes |
| 6 hours | 15 minutes |
| 24 hours | 1 hour |
| 7 days | 6 hours |
| 30 days | 1 day |

Buckets are computed in UTC; the chart labels use the browser time zone. Naive timestamp
columns (`messages.timestamp`, `chat_sessions.created_at`, `documents.uploaded_at`) hold local
time and are converted with the configured `TIMEZONE` (UTC when it is unset).

### Storage history

Storage is the only sampled metric. A background thread writes one `storage_snapshots` row per
user per hour (idempotent per hour, retention is unlimited). The chart carries the last known
value forward, so a gap in snapshots never draws a drop to zero, and a bucket before the first
snapshot is a gap (`null`), not zero. Each bucket reports the value as of its end, so a daily
bucket picks up every snapshot taken during that day. On a fresh installation the card states
the date of the first snapshot.

### Deleted activity is kept

Deleting a chat session, clearing a chat, deleting a task or deleting a document would
otherwise erase real activity from the charts. Instead, the same transaction that deletes the
rows writes a minimal entry to `monitoring_archive` (`user_id`, kind, original timestamp,
tokens, attachment flag), and every chart sums live rows and archived rows. Consequences:

- Monitoring reports **activity that happened**, while the Users tab reports **data that still
  exists**; a deleted session appears in Monitoring and not in the Users tab.
- The archive is pruned after 45 days (the longest range plus a margin).
- Deleting an account erases its archive and snapshots too (GDPR), and both tables are included
  in full backups.

### Endpoint

`GET /admin/api/monitoring?user=<login|all>&range=<1h|6h|24h|7d|30d>` (admin only) returns every
series in one payload: `buckets`, `series`, `totals`, `storage` (`current_bytes`,
`limit_bytes`, `since`) and the selectable `users`. An unknown user or range is a `400`. The
payload is cached in Redis for 60 seconds; without Redis the answer is computed directly.
