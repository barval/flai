import contextlib
import logging
import os

import click
from flask import current_app
from flask.cli import with_appcontext

from app.config import setting
from app.userdb import create_user, get_user_by_login, update_password

logger = logging.getLogger(__name__)


@click.command("admin-password")
@click.argument("password")
@with_appcontext
def set_admin_password(password):
    """Set the admin password (creates admin user if it doesn't exist)."""
    admin = get_user_by_login("admin")
    if admin:
        update_password("admin", password)
        click.echo("Admin password changed.")
    else:
        create_user(login="admin", password=password, name="Administrator", service_class=0, is_admin=True)
        click.echo("Admin created.")


def _upload_owners(cursor) -> dict[str, str]:
    """Map every session/task upload directory to its owning login."""
    owners: dict[str, str] = {}
    cursor.execute("SELECT id, user_id FROM chat_sessions")
    for row in cursor.fetchall():
        if row.get("user_id"):
            owners[str(row["id"])] = str(row["user_id"])
    cursor.execute("SELECT id::text AS id, user_id FROM tasks")
    for row in cursor.fetchall():
        if row.get("user_id"):
            owners[str(row["id"])] = str(row["user_id"])
    return owners


def _storage_by_owner(
    upload_folder: str, owners: dict[str, str], exclude: set[str] | None = None
) -> tuple[dict[str, int], int, int]:
    """Sum the files of every owned upload directory, ignoring symlinked dirs."""
    usage: dict[str, int] = {}
    unknown_dirs = 0
    unknown_bytes = 0
    excluded = exclude or set()
    for name in os.listdir(upload_folder):
        path = os.path.join(upload_folder, name)
        if not os.path.isdir(path) or os.path.islink(path):
            continue
        size = 0
        for root, _dirs, files in os.walk(path):
            for filename in files:
                full = os.path.join(root, filename)
                if os.path.relpath(full, upload_folder) in excluded:
                    continue
                with contextlib.suppress(OSError):
                    size += os.path.getsize(full)
        owner = owners.get(name)
        if owner:
            usage[owner] = usage.get(owner, 0) + size
        elif size:
            unknown_dirs += 1
            unknown_bytes += size
    return usage, unknown_dirs, unknown_bytes


def _recalculate_storage(upload_folder: str, owners: dict[str, str]) -> dict[str, int]:
    """Rebuild user_storage from the files that actually remain on disk.

    Counters were never decreased when an upload directory was deleted outside
    session deletion (a deleted task, or cleanup-uploads itself), so a user
    could stay over quota with an empty account. Recomputing from disk repairs
    that historical drift; live paths always update the counter incrementally.
    """
    from app.database import get_db

    usage, unknown_dirs, unknown_bytes = _storage_by_owner(upload_folder, owners)
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT user_id FROM user_storage")
        known = {str(row["user_id"]) for row in c.fetchall()}
        for user_id in sorted(set(usage) | known):
            c.execute(
                """
                INSERT INTO user_storage (user_id, used_bytes)
                VALUES (%s, %s)
                ON CONFLICT(user_id) DO UPDATE SET used_bytes = EXCLUDED.used_bytes
                """,
                (user_id, int(usage.get(user_id, 0))),
            )
    if unknown_dirs:
        click.echo(
            f"WARNING: {unknown_dirs} upload directories have no owner in the DB "
            f"({unknown_bytes} bytes) — their quota cannot be attributed."
        )
    return usage


@click.command("cleanup-uploads")
@click.option("--dry-run", is_flag=True, help="Only list orphans, do not delete")
@with_appcontext
def cleanup_uploads(dry_run):
    """Remove orphaned files from uploads/ and rebuild the storage counters."""
    from app.database import get_db

    upload_folder = current_app.config.get("UPLOAD_FOLDER", "data/uploads")
    if not os.path.isabs(upload_folder):
        upload_folder = os.path.join(current_app.root_path, "..", upload_folder)
    upload_folder = os.path.abspath(upload_folder)

    click.echo(f"Scanning {upload_folder} ...")

    # Collect all file paths referenced in DB: the file_path column plus
    # file_path fields inside message content JSON (extra multi-attachment
    # chat images are stored there).
    db_files = set()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT file_path, content FROM messages WHERE file_path IS NOT NULL OR content IS NOT NULL")
        import json

        for row in c.fetchall():
            if row["file_path"]:
                db_files.add(row["file_path"])
            content = row["content"]
            if not content:
                continue
            try:
                parsed = json.loads(content)
            except Exception:
                continue
            if not isinstance(parsed, list):
                continue
            for item in parsed:
                if isinstance(item, dict) and item.get("file_path"):
                    db_files.add(item["file_path"])

        # Task media live in task_steps.extra ({"media": {"file_path": ...}})
        # — a running task's generated files must survive the cleanup.
        try:
            c.execute("SELECT extra FROM task_steps WHERE extra IS NOT NULL")
            for row in c.fetchall():
                try:
                    extra = json.loads(row["extra"]) if isinstance(row["extra"], str) else (row["extra"] or {})
                except Exception:
                    continue
                if not isinstance(extra, dict):
                    continue
                media = extra.get("media") or {}
                if isinstance(media, dict) and media.get("file_path"):
                    db_files.add(media["file_path"])
                # Candidates of an in-flight step are still live: the retry loop
                # edits them and the best-of fallback ranks them.
                for candidate in extra.get("candidates") or []:
                    if isinstance(candidate, dict) and candidate.get("file_path"):
                        db_files.add(candidate["file_path"])
        except Exception:
            # The tasks tables may not exist in legacy DBs — cleanup still runs.
            pass

        # Images and audio attached to a task live in the same task directory
        # but are referenced only from the tasks row, not from a step.
        try:
            c.execute("SELECT images, audio FROM tasks")
            for row in c.fetchall():
                for key in ("images", "audio"):
                    items = row[key] or []
                    if not isinstance(items, list):
                        continue
                    for item in items:
                        if isinstance(item, dict) and item.get("file_path"):
                            db_files.add(item["file_path"])
        except Exception:
            # Legacy DBs without these JSONB columns — cleanup still runs.
            pass

    click.echo(f"Files referenced in DB: {len(db_files)}")

    # A task that is still queued/running writes new artifacts between this
    # scan and the walk below. Its directory is live work, not an orphan: skip
    # it entirely instead of racing the executor (the quota still counts it).
    protected_dirs: set[str] = set()
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT id::text AS id FROM tasks "
                "WHERE status NOT IN ('completed', 'failed', 'expired', 'cancelled')"
            )
            protected_dirs = {str(row["id"]) for row in c.fetchall()}
    except Exception:
        # Legacy DBs without the tasks table — nothing to protect.
        protected_dirs = set()

    # Walk upload dir and find orphans
    deleted_files = 0
    deleted_bytes = 0
    deleted_rels: set[str] = set()
    for session_dir in os.listdir(upload_folder):
        session_path = os.path.join(upload_folder, session_dir)
        if not os.path.isdir(session_path):
            continue
        if session_dir in protected_dirs:
            continue
        for fname in os.listdir(session_path):
            fpath = os.path.join(session_dir, fname)
            full = os.path.join(upload_folder, fpath)
            if not os.path.isfile(full):
                continue
            if fpath not in db_files:
                fsize = os.path.getsize(full)
                click.echo(f"  ORPHAN {fpath} ({fsize} bytes)")
                deleted_rels.add(fpath)
                if not dry_run:
                    os.remove(full)
                deleted_files += 1
                deleted_bytes += fsize

    # Remove empty directories
    empty_dirs = 0
    for session_dir in os.listdir(upload_folder):
        session_path = os.path.join(upload_folder, session_dir)
        if session_dir in protected_dirs:
            continue
        if os.path.isdir(session_path) and not os.listdir(session_path):
            click.echo(f"  EMPTY DIR {session_dir}/")
            if not dry_run:
                os.rmdir(session_path)
            empty_dirs += 1

    click.echo(f"\nDeleted {deleted_files} orphan files ({deleted_bytes} bytes), {empty_dirs} empty dirs")
    if dry_run:
        click.echo("(dry-run, no changes made)")

    # Rebuild the quota counters from what actually remains. Cleanup used to
    # delete files without touching user_storage, so an emptied account stayed
    # over quota forever.
    with get_db() as conn:
        owners = _upload_owners(conn.cursor())
    if dry_run:
        usage, _unknown_dirs, _unknown_bytes = _storage_by_owner(upload_folder, owners, exclude=deleted_rels)
        click.echo("Storage after cleanup (preview):")
        for user_id, used in sorted(usage.items()):
            click.echo(f"  {user_id}: {used / (1024 * 1024):.1f} MB")
    else:
        usage = _recalculate_storage(upload_folder, owners)
        click.echo("Storage recalculated:")
        for user_id, used in sorted(usage.items()):
            click.echo(f"  {user_id}: {used / (1024 * 1024):.1f} MB")


@click.command("migrate-messages-format")
@click.option("--dry-run", is_flag=True, help="Only list messages to convert, do not update")
@click.option("--add-emojis", is_flag=True, help="Add 🎨 emoji to image-related service prefixes")
@with_appcontext
def migrate_messages_format(dry_run, add_emojis):
    """Migrate old plain-text service messages to new JSON {prefix, text} format.

    With --add-emojis, also add 🎨 prefix to existing image gen/edit messages
    that are missing it (both EN and RU), regardless of format (plain or JSON).
    """
    import json

    from app.database import get_db

    # Known prefixes for service messages (EN + RU)
    prefixes_whisper = ["🎤 Transcribed: ", "🎤 Распознано: "]
    prefixes_image = [
        "Image generated from request: ",
        "Изображение сгенерировано по запросу: ",
        "Image edited from request: ",
        "Изображение отредактировано по запросу: ",
    ]
    prefixes_other = [
        "🎤 Transcribed: ",
        "🎤 Распознано: ",
        *prefixes_image,
        "Camera snapshot: ",
        "Снимок с камеры: ",
    ]

    updated = 0
    skipped = 0

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, content, model_name FROM messages WHERE role = 'assistant'")

        for row in c.fetchall():
            msg_id = row["id"]
            content = row["content"]
            model_name = row["model_name"]

            if not content:
                continue

            # --- JSON path (already in {prefix, text} format) ---
            if content.startswith("{") and content.endswith("}"):
                try:
                    parsed = json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    parsed = None

                if parsed and "prefix" in parsed and "text" in parsed:
                    if add_emojis:
                        old_prefix = parsed["prefix"]
                        # Check if this is an image prefix without 🎨
                        is_image = any(old_prefix == p or old_prefix.startswith(p) for p in prefixes_image)
                        if is_image and "🎨 " not in old_prefix:
                            parsed["prefix"] = "🎨 " + old_prefix
                            new_content = json.dumps(parsed, ensure_ascii=False)
                            click.echo(f"  EMOJI  id={msg_id}: {old_prefix!r} → {parsed['prefix']!r}")
                            if not dry_run:
                                c.execute(
                                    "UPDATE messages SET content = %s WHERE id = %s",
                                    (new_content, msg_id),
                                )
                                conn.commit()
                            updated += 1
                        else:
                            skipped += 1
                    else:
                        skipped += 1
                    continue

            # --- Plain-text path ---
            # Choose prefix candidates based on model_name
            candidates = prefixes_whisper if model_name == "whisper" else prefixes_other

            matched = False
            for prefix in candidates:
                if content.startswith(prefix):
                    text = content[len(prefix) :]
                    final_prefix = prefix
                    # Add 🎨 to image prefixes when --add-emojis is set
                    if add_emojis and any(prefix.startswith(p) for p in prefixes_image):
                        final_prefix = "🎨 " + prefix
                    new_content = json.dumps({"prefix": final_prefix, "text": text}, ensure_ascii=False)
                    click.echo(f"  MIGRATE id={msg_id}: {prefix!r} → {new_content[:80]}...")
                    if not dry_run:
                        c.execute(
                            "UPDATE messages SET content = %s WHERE id = %s",
                            (new_content, msg_id),
                        )
                        conn.commit()
                    updated += 1
                    matched = True
                    break

            if not matched:
                skipped += 1

    click.echo(f"\nUpdated: {updated}, Skipped (already JSON or no match): {skipped}")
    if dry_run:
        click.echo("(dry-run, no changes made)")


@click.command("import-history-to-slm")
@click.option("--dry-run", is_flag=True, help="Only show what would be imported, do not save")
@click.option("--force", is_flag=True, help="Ignore checkpoints, re-import all messages")
@click.argument("user_id", required=False)
@with_appcontext
def import_history_to_slm(dry_run, force, user_id):
    """Import existing conversation history into SuperLocalMemory.

    Reads user queries and assistant responses from the database
    and saves them as facts in SLM for long-term memory retrieval.

    By default, respects per-user checkpoints (slm_import_progress table)
    and only imports new/unprocessed messages. Use --force to reset and
    re-import all messages.

    If USER_ID is provided, imports only that user's history.
    Without USER_ID, imports all users' history.
    """
    from app.slm_import import import_all_users, import_user_messages

    slm = current_app.modules.get("slm")
    if not slm or not slm.available:
        click.echo("Error: SLM module is not available. Is SLM_URL configured and flai-slm running?")
        return

    if force:
        click.echo("Force mode: ignoring checkpoints, will re-import all messages")
        since = 0
    else:
        from app.slm_import import _get_last_message_id as get_checkpoint

        since = get_checkpoint(user_id) if user_id else 0

    if user_id:
        click.echo(f"Importing history for user: {user_id}")
        imported, skipped, last_id = import_user_messages(slm, user_id, since_message_id=since, dry_run=dry_run)
        click.echo(f"Imported: {imported}, Skipped: {skipped}, Last message ID: {last_id}")
    else:
        click.echo("Importing history for all users...")
        results = import_all_users(slm, dry_run=dry_run)
        total_imported = sum(r[0] for r in results.values())
        total_skipped = sum(r[1] for r in results.values())
        click.echo(f"Total imported: {total_imported}, Total skipped: {total_skipped}")
        for uid, (imp, skip, lid) in sorted(results.items()):
            click.echo(f"  {uid}: {imp} imported, {skip} skipped, up to msg {lid}")

    if dry_run:
        click.echo("(dry-run, no changes made)")


@click.command("cleanup-slm")
@click.option("--user", "user_id", required=True, help="User ID to cleanup")
@click.option("--dry-run", is_flag=True, help="Count without deleting")
@with_appcontext
def cleanup_slm(user_id, dry_run):
    """Remove junk facts (model responses) from SLM for a user."""
    from app.slm_merge import _is_model_response

    slm = current_app.modules.get("slm")
    if not slm or not slm.available:
        click.echo("SLM module not available")
        return

    facts = slm.list_facts(limit=100, profile=user_id)
    if not facts:
        click.echo(f"No facts found for user: {user_id}")
        return

    junk = [f for f in facts if _is_model_response(f.get("content", ""))]

    click.echo(f"User: {user_id}")
    click.echo(f"Total facts: {len(facts)}")
    click.echo(f"Junk (model responses): {len(junk)}")
    click.echo(f"Real facts remaining: {len(facts) - len(junk)}")

    if junk:
        click.echo("\nJunk facts to remove:")
        for f in junk[:20]:
            content = f.get("content", "")
            click.echo(f"  - {content[:80]}...")

    if dry_run:
        click.echo("\n(dry-run, no changes made)")
        return

    removed = 0
    for f in junk:
        fid = f.get("fact_id") or f.get("id")
        if fid:
            slm.delete_fact(fid, user_id)
            removed += 1

    click.echo(f"\nRemoved {removed} junk facts.")


@click.command("reset-slm-checkpoint")
@click.argument("user_id", required=False)
@with_appcontext
def reset_slm_checkpoint(user_id):
    """Reset SLM import checkpoints so next import re-processes all messages.

    If USER_ID is provided, resets only that user's checkpoint.
    Without USER_ID, resets all users' checkpoints.
    """
    from app.database import get_db

    with get_db() as conn:
        c = conn.cursor()
        if user_id:
            c.execute("DELETE FROM slm_import_progress WHERE user_id = %s", (user_id,))
            click.echo(f"Reset checkpoint for user: {user_id}")
        else:
            c.execute("DELETE FROM slm_import_progress")
            click.echo("Reset checkpoints for all users")
        conn.commit()


@click.command("backfill-document-blobs")
@click.option("--dry-run", is_flag=True, help="Only report what would change, do not move files")
@with_appcontext
def backfill_document_blobs(dry_run):
    """Move documents uploaded before content addressing into the blob store.

    Documents stored before v13.1 keep their own file and no blob_sha256, so a
    later upload of the same content cannot reuse them. This computes each file's
    SHA-256, moves it to DOCUMENTS_FOLDER/_blobs/<sha><ext>, registers the blob
    and points the row at it. Quota is untouched: stored_bytes keeps the size the
    owner already paid for. Idempotent - rows with a blob are skipped. Run a
    database backup first.

    Two rules keep this safe, and both were learned the hard way: a dry run never
    writes to the database (an earlier version registered blobs it had not
    stored), and a source file is deleted only after the target file is confirmed
    to exist (that same version deleted documents whose blob row existed while
    the bytes had never been written).
    """
    import hashlib
    import shutil

    from app import db

    folder = current_app.config.get("DOCUMENTS_FOLDER", "data/documents")
    blobs_folder = os.path.join(folder, "_blobs")

    with db.get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, user_id, file_path, file_size, file_ext FROM documents WHERE blob_sha256 IS NULL")
        rows = c.fetchall()

    if not rows:
        click.echo("Nothing to do: every document already points at a blob.")
        return

    moved = reused = missing = 0
    for row in rows:
        relative = row["file_path"] or ""
        source = os.path.join(folder, relative)
        if not relative or not os.path.exists(source):
            missing += 1
            click.echo(f"  missing file, left alone: {row['id']} ({relative or 'no path'})")
            continue
        with open(source, "rb") as handle:
            sha = hashlib.sha256(handle.read()).hexdigest()
        target_relative = os.path.join("_blobs", f"{sha}{row['file_ext'] or ''}")
        target = os.path.join(folder, target_relative)
        target_exists = os.path.exists(target)
        label = "reused" if target_exists else "new blob"
        click.echo(f"  {'[dry-run] ' if dry_run else ''}{row['id']}: {relative} -> {target_relative} ({label})")
        if dry_run:
            continue

        os.makedirs(blobs_folder, exist_ok=True)
        if not os.path.exists(target):
            shutil.move(source, target)
            moved += 1
        else:
            reused += 1
            if os.path.realpath(source) != os.path.realpath(target):
                os.remove(source)
        if not os.path.exists(target):
            click.echo(f"  refused to register {row['id']}: the target file is missing")
            continue
        db.retain_document_blob(sha, target_relative, row["file_size"] or 0)
        with db.get_db() as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE documents SET blob_sha256 = %s, file_path = %s, stored_bytes = %s WHERE id = %s",
                (sha, target_relative, row["file_size"] or 0, row["id"]),
            )

    click.echo(
        f"{'Would move' if dry_run else 'Moved'} {moved} file(s), reused {reused} existing blob(s), "
        f"{missing} missing file(s)."
    )


@click.command("backfill-attachment-paths")
@click.option("--dry-run", is_flag=True, help="Only report what would change, do not update")
@with_appcontext
def backfill_attachment_paths(dry_run):
    """Save base64 attachment payloads from old messages to disk and keep paths.

    Legacy chat rows stored attachment payloads (images, audio, documents)
    directly inside the content JSON without a part-level file_path, so opening
    such a session shipped megabytes of base64. This command saves every such
    part to UPLOAD_FOLDER via save_uploaded_file, replaces the payload with the
    saved relative file_path, and clears the now-redundant column base64.
    Idempotent: re-running it changes nothing. Run a database backup first.
    """
    import json

    from app.database import get_db
    from app.utils import save_uploaded_file

    upload_folder = current_app.config.get("UPLOAD_FOLDER", "data/uploads")

    updated = 0
    skipped = 0

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, session_id, content, file_path, file_data FROM messages WHERE content LIKE '%file_data%'")

        for row in c.fetchall():
            try:
                parsed = json.loads(row["content"])
            except (TypeError, ValueError):
                parsed = None
            if not isinstance(parsed, list):
                skipped += 1
                continue

            new_file_path = row["file_path"]
            needs_update = False
            first_part = True
            for item in parsed:
                if not isinstance(item, dict) or "file_data" not in item:
                    continue
                is_primary = first_part
                first_part = False
                if is_primary and not item.get("file_path") and row["file_path"]:
                    # The primary attachment already has a saved file at the row
                    # level: point the part at it and drop the payload.
                    item["file_path"] = row["file_path"]
                    del item["file_data"]
                    needs_update = True
                    continue
                if item.get("file_path"):
                    # Part already references a saved file: drop the payload.
                    del item["file_data"]
                    needs_update = True
                    continue
                if item.get("file_data") is None:
                    del item["file_data"]
                    needs_update = True
                    continue
                if dry_run:
                    # Report-only: the payload stays in place and no file is
                    # written.
                    needs_update = True
                    continue
                try:
                    saved_path = save_uploaded_file(
                        file_data=item["file_data"],
                        filename=item.get("file_name") or "attachment.bin",
                        session_id=row["session_id"],
                        upload_folder=upload_folder,
                    )
                except Exception as exc:
                    logger.warning(f"backfill: message {row['id']}: failed to save attachment: {exc}")
                    saved_path = None
                if not saved_path:
                    skipped += 1
                    continue
                item["file_path"] = saved_path
                del item["file_data"]
                needs_update = True
                if is_primary and not new_file_path:
                    new_file_path = saved_path

            if not needs_update:
                skipped += 1
                continue

            new_content = json.dumps(parsed, ensure_ascii=False)
            if dry_run:
                click.echo(f"  WOULD BACKFILL id={row['id']}")
                updated += 1
                continue
            click.echo(f"  BACKFILL id={row['id']}")
            if new_content != row["content"]:
                c.execute(
                    "UPDATE messages SET content = %s, file_path = %s,"
                    " file_data = CASE WHEN %s IS NULL THEN file_data ELSE NULL END"
                    " WHERE id = %s",
                    (new_content, new_file_path, new_file_path, row["id"]),
                )
            else:
                c.execute(
                    "UPDATE messages SET file_path = %s,"
                    " file_data = CASE WHEN %s IS NULL THEN file_data ELSE NULL END"
                    " WHERE id = %s",
                    (new_file_path, new_file_path, row["id"]),
                )
            conn.commit()
            updated += 1

        if not dry_run:
            # Clear any remaining column base64 for rows that already have a file.
            c.execute("UPDATE messages SET file_data = NULL WHERE file_path IS NOT NULL AND file_data IS NOT NULL")
            conn.commit()

    if dry_run:
        click.echo(f"\nWould update: {updated}, Skipped: {skipped}")
        click.echo("(dry-run, no changes made)")
        return

    click.echo(f"\nUpdated: {updated}, Skipped: {skipped}")


@click.command("rag-eval")
@click.option("--user", required=True, help="Login whose indexed documents are searched")
@click.option("--dataset", required=True, type=click.Path(exists=True), help="JSONL question set")
@click.option("--top-k", default=10, type=int, show_default=True)
@click.option("--coverage", is_flag=True, help="Enable the file-coverage pass during search")
@click.option("--answers", is_flag=True, help="End-to-end mode: answer each question, compare with its 'answer' field")
@click.option(
    "--judge",
    is_flag=True,
    help="Grade each answer with the reasoning model instead of an exact match (implies --answers)",
)
@click.option("--model", type=click.Choice(["multimodal", "reasoning"]), default="multimodal", show_default=True)
@click.option("--scope-folder", default="", help="Restrict retrieval to one folder id")
@click.option("--scope-docs", default="", help="Comma-separated document ids to restrict retrieval to")
@click.option("--label", default="", help="Report label, e.g. 'dense baseline'")
@click.option("--out", default="", help="Report path (default: data/rag_eval/<timestamp>.json)")
@click.option("--budget", is_flag=True, help="Let the derived budget choose top_k instead of --top-k")
@click.option("--answer-share", type=float, default=None, help="Override ANSWER_SHARE for this run (sweep)")
@click.option("--depth-over-coverage", type=float, default=None, help="Override DEPTH_OVER_COVERAGE (sweep)")
@click.option("--breadth-share", type=float, default=None, help="Override BREADTH_SHARE (sweep)")
@with_appcontext
def rag_eval(
    user,
    dataset,
    top_k,
    coverage,
    answers,
    judge,
    model,
    scope_folder,
    scope_docs,
    label,
    out,
    budget,
    answer_share,
    depth_over_coverage,
    breadth_share,
):
    """Measure retrieval quality (or end-to-end answer quality) on a fixed question set."""
    from datetime import datetime

    from app.rag_eval import evaluate, evaluate_answers, format_report, load_dataset, save_report

    rag = current_app.modules.get("rag")
    if rag is None:
        raise click.ClickException("RAG module is not available")
    if not rag.check_availability():
        raise click.ClickException("Qdrant is not reachable")

    if judge:
        # Exact comparison counts an answer as a miss when only the capital
        # letter and the full stop differ, so a judged run always measures
        # answers.
        answers = True
    items = load_dataset(dataset)
    click.echo(f"Loaded {len(items)} questions from {dataset}")

    scope: dict = {}
    if scope_folder:
        scope["folder"] = scope_folder
    if scope_docs:
        scope["docs"] = [value.strip() for value in scope_docs.split(",") if value.strip()]
    scope_ids = None
    if scope:
        from app.rag_filters import resolve_scope

        scope_ids = resolve_scope(user, scope)
        click.echo(f"Scope: {len(scope_ids or [])} document(s)")

    # The policy constants are module-level; a sweep overrides them for the run
    # only, and the report records what was actually used.
    import app.context_budget as context_policy
    import app.rag_budget as retrieval_policy

    overrides = {}
    if answer_share is not None:
        context_policy.ANSWER_SHARE = answer_share
        overrides["ANSWER_SHARE"] = answer_share
    if depth_over_coverage is not None:
        retrieval_policy.DEPTH_OVER_COVERAGE = depth_over_coverage
        overrides["DEPTH_OVER_COVERAGE"] = depth_over_coverage
    if breadth_share is not None:
        retrieval_policy.BREADTH_SHARE = breadth_share
        overrides["BREADTH_SHARE"] = breadth_share

    def search(query: str, limit: int) -> list[dict]:
        chunks, _scores = rag.search(
            user,
            query,
            # --budget lets the allocator decide; otherwise the fixed k is used.
            top_k=None if budget else limit,
            file_coverage=coverage,
            doc_ids=scope_ids,
        )
        return chunks

    config = {
        key: current_app.config.get(key)
        for key in ("RAG_TOP_K", "RAG_CHUNK_SIZE", "RAG_CHUNK_OVERLAP", "RAG_CHUNK_STRATEGY", "RAG_CONTEXT_PERCENT")
    }
    config["coverage"] = coverage
    config["budget"] = budget
    config.update({f"override_{key}": value for key, value in overrides.items()})
    run_label = label or f"{user} @ {datetime.now():%Y-%m-%d %H:%M}"

    if answers:
        base = current_app.modules.get("base")
        if base is None:
            raise click.ClickException("LLM module is not available")
        instruction = (
            "Ответь на вопрос, используя ТОЛЬКО фрагменты ниже. Если вопрос требует подсчёта, "
            "пересчитай подходящие записи по фрагментам и ответь числом. Если ответа в них нет, "
            "ответь ровно: НЕТ ДАННЫХ. Ответь одной короткой фразой, без пояснений."
        )

        def answer_fn(question: str, context: str) -> str:
            prompt = f"{instruction}\n\nВОПРОС: {question}\n\nФРАГМЕНТЫ:\n{context}"
            reply = base.call_llamacpp(
                [{"role": "user", "content": prompt}],
                model_type=model,
                lang="ru",
                temperature=0.1,
            )
            return str(reply or "").strip()

        judge_fn = None
        if judge:

            def judge_fn(prompt: str) -> str:
                # The run's own model, not always the reasoning one: a 35B model
                # and the embedding model do not fit a 16 GB card together, so a
                # run that alternates search and answers evicted it mid-way and
                # twenty of thirty answers came back as a memory notice.
                return str(
                    base.call_llamacpp(
                        [{"role": "user", "content": prompt}],
                        model_type=model,
                        lang="ru",
                        temperature=0,
                    )
                    or ""
                )

        report = evaluate_answers(
            items, answer_fn, search, top_k=top_k, label=run_label, config=config, judge_fn=judge_fn
        )
    else:
        report = evaluate(items, search, top_k=top_k, label=run_label, config=config)
    click.echo("")
    click.echo(format_report(report))

    target = out or f"data/rag_eval/{datetime.now():%Y%m%d-%H%M%S}.json"
    saved = save_report(report, target)
    click.echo(f"\nReport saved: {saved}")


@click.command("rag-eval-judge")
@click.option("--report", required=True, type=click.Path(exists=True), help="A saved answers report to re-grade")
@click.option("--model", type=click.Choice(["multimodal", "reasoning"]), default="multimodal", show_default=True)
@click.option("--out", default="", help="Report path (default: overwrite the input report)")
@with_appcontext
def rag_eval_judge(report, model, out):
    """Grade the answers of a saved report with the model.

    Grading belongs in its own pass: interleaving it with retrieval made every
    request alternate between the embedding and the chat model, and on a 16 GB
    card the chat model was evicted between two calls, so most verdicts came
    back empty.
    """
    import json
    from pathlib import Path

    from app.rag_eval import build_judge_prompt, parse_verdict, save_report

    base = current_app.modules.get("base")
    if base is None:
        raise click.ClickException("LLM module is not available")
    data = json.loads(Path(report).read_text(encoding="utf-8"))
    rows = data.get("results") or []
    if not rows or data.get("mode") != "answers":
        raise click.ClickException("The report carries no answers to grade")

    graded = 0
    for row in rows:
        reply = base.call_llamacpp(
            [{"role": "user", "content": build_judge_prompt(row["question"], str(row["expected"]), str(row["actual"]))}],
            model_type=model,
            lang="ru",
            temperature=0,
        )
        row["judge"] = parse_verdict(reply)
        if row["judge"]["score"] >= 0:
            graded += 1

    scores = [row["judge"]["score"] for row in rows if row["judge"]["score"] >= 0]
    data["metrics"]["judge_score"] = (sum(scores) / (2 * len(scores))) if scores else 0.0
    data["metrics"]["judge_full"] = (sum(1 for score in scores if score == 2) / len(scores)) if scores else 0.0
    data["metrics"]["judge_failed"] = len(rows) - graded
    target = out or report
    saved = save_report(data, target)
    click.echo(f"graded {graded}/{len(rows)} answers")
    click.echo(f"judge score: {data['metrics']['judge_score']:.3f}  (fully right {data['metrics']['judge_full']:.3f})")
    click.echo(f"Report saved: {saved}")


@click.command("rag-eval-generate")
@click.option("--user", required=True, help="Login whose indexed documents seed the questions")
@click.option("--count", default=20, type=int, show_default=True)
@click.option("--lang", default="ru", show_default=True)
@click.option("--model", type=click.Choice(["multimodal", "reasoning"]), default="multimodal", show_default=True)
@click.option("--doc", default="", help="Comma-separated document ids to sample from (default: all)")
@click.option("--out", required=True, type=click.Path(), help="JSONL output path")
@with_appcontext
def rag_eval_generate(user, count, lang, model, doc, out):
    """Build a question set from the user's indexed chunks (one LLM call each)."""
    import json
    import random
    from pathlib import Path

    rag = current_app.modules.get("rag")
    base = current_app.modules.get("base")
    if rag is None or base is None:
        raise click.ClickException("RAG or LLM module is not available")
    if not rag.check_availability():
        raise click.ClickException("Qdrant is not reachable")

    collection = rag._get_collection_name(user)
    scroll_filter = None
    wanted_docs = [value.strip() for value in str(doc or "").split(",") if value.strip()]
    if wanted_docs:
        from qdrant_client import models as qmodels

        scroll_filter = qmodels.Filter(
            must=[qmodels.FieldCondition(key="doc_id", match=qmodels.MatchAny(any=wanted_docs))]
        )
    points, _offset = rag.qdrant_client.scroll(
        collection_name=collection, limit=max(count * 5, 50), with_payload=True, scroll_filter=scroll_filter
    )
    chunks = [point.payload for point in points if (point.payload or {}).get("text")]
    random.shuffle(chunks)
    if not chunks:
        raise click.ClickException(f"No indexed chunks for {user}")

    instruction = (
        "Представь, что человек ищет этот фрагмент в базе документов. Задай один конкретный вопрос, "
        "на который фрагмент полностью отвечает. Сформулируй его своими словами, как спросил бы живой "
        "человек: не копируй формулировки и редкие термины из текста, без цитат. Верни только вопрос."
        if lang == "ru"
        else "Imagine a person searching a document base for this passage. Write one specific question "
        "that the passage fully answers, in your own words as a real user would ask: do not copy the "
        "wording or rare terms from the text, no quotes. Return only the question."
    )
    items: list[dict] = []
    seen: set[str] = set()
    for chunk in chunks:
        if len(items) >= count:
            break
        text = str(chunk.get("text") or "").strip()
        if len(text) < 200:
            continue
        answer = base.call_llamacpp(
            [{"role": "user", "content": f"{instruction}\n\n---\n{text[:1500]}"}],
            model_type=model,
            lang=lang,
            temperature=0.3,
        )
        question = str(answer or "").strip().strip('"')
        # The model occasionally returns a backend error instead of a question.
        if question.startswith("⚠️") or "http error" in question.lower()[:60]:
            continue
        if not question or len(question) < 12 or question.lower() in seen:
            continue
        seen.add(question.lower())
        items.append(
            {
                "question": question,
                "relevant": [str(chunk.get("doc_id") or chunk.get("filename") or "")],
                "reference": text[:200],
            }
        )
        click.echo(f"[{len(items)}/{count}] {question}")

    if not items:
        raise click.ClickException("No questions were generated")
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in items), encoding="utf-8")
    click.echo(f"\nSaved {len(items)} questions to {target}")


@click.command("rag-backfill-chunks")
@click.option("--user", required=True, help="Login whose indexed chunks are mirrored")
@with_appcontext
def rag_backfill_chunks(user):
    """Mirror already-indexed chunks into PostgreSQL for hybrid search."""
    from app.database import get_db
    from app.lemmatizer import lemmas as lemmatize

    rag = current_app.modules.get("rag")
    if rag is None or not rag.check_availability():
        raise click.ClickException("RAG module or Qdrant is not available")

    collection = rag._get_collection_name(user)
    by_doc: dict[str, list[tuple[int, str, str]]] = {}
    offset = None
    while True:
        points, offset = rag.qdrant_client.scroll(
            collection_name=collection, limit=200, offset=offset, with_payload=True
        )
        for point in points:
            payload = point.payload or {}
            doc_id = str(payload.get("doc_id") or "")
            if not doc_id:
                continue
            by_doc.setdefault(doc_id, []).append(
                (
                    int(payload.get("chunk_index") or 0),
                    str(payload.get("text") or ""),
                    str(payload.get("filename") or ""),
                )
            )
        if offset is None:
            break

    written = 0
    with get_db() as conn:
        cursor = conn.cursor()
        for doc_id, rows in by_doc.items():
            rows.sort()
            filename = rows[0][2]
            cursor.execute("DELETE FROM rag_chunks WHERE user_id = %s AND doc_id = %s", (user, doc_id))
            for index, text, _name in rows:
                cursor.execute(
                    "INSERT INTO rag_chunks (user_id, doc_id, chunk_index, filename, text, lemmas) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (user, doc_id, index, filename, text, lemmatize(text)),
                )
                written += 1

    click.echo(f"Mirrored {written} chunks from {len(by_doc)} documents for {user}")


@click.command("vram-probe")
@click.option(
    "--module",
    default="multimodal",
    show_default=True,
    type=click.Choice(["multimodal", "reasoning", "embedding"]),
    help="Module whose model is measured",
)
@click.option("--peak", is_flag=True, help="Also send a long prompt and record the prefill peak")
@click.option("--peak-chars", default=16000, show_default=True, help="Prompt size for the peak run")
@click.option("--store", is_flag=True, help="Write the result into model_vram_estimates")
@with_appcontext
def vram_probe(module, peak, peak_chars, store):
    """Measure one model's real VRAM footprint (weights, vision encoder, KV, peak).

    The analytical constant is a guess per module; the fitted context window and
    the KV quantisation decide the truth. Measured numbers are what a
    co-residency decision has to use — loading into the last megabyte lets the
    model spill into host memory, which costs far more than a reload.
    """
    import os

    from app.database import upsert_vram_estimate
    from app.model_config import get_model_config
    from app.vram_probe import probe_module

    swap_url = os.getenv("LLAMA_SWAP_URL", "http://flai-llamaswap:8080").rstrip("/")
    result = probe_module(module, swap_url, peak_prompt_chars=peak_chars if peak else 0)

    config = get_model_config(module) or {}
    click.echo(f"module:    {result['module']} ({result['model']})")
    click.echo(f"context:   {result['ctx_size']}")
    click.echo(f"baseline:  {result['baseline_mb']} MB")
    click.echo(f"total:     {result['total_mb']} MB")
    click.echo(f"weights:   {result['weights_mb']} MB")
    click.echo(f"mmproj:    {result['mmproj_mb']} MB")
    click.echo(f"kv+bufs:   {result['kv_and_buffers_mb']} MB (residual, includes buffers)")
    click.echo(f"kv shapes: {result['kv_expected_mb']} MB expected from the GGUF architecture")

    if not result["plausible"]:
        click.echo("measurement unusable: the total is below the model's own weights — nothing stored")
        return

    if store:
        upsert_vram_estimate(
            module,
            model_name=result["model"] or "",
            context_length=result["ctx_size"],
            n_gpu_layers=int(config.get("n_gpu_layers") or -1),
            measured_mb=result["total_mb"],
        )
        click.echo("stored in model_vram_estimates")


@click.command("vram-plan")
@click.option(
    "--module",
    default="reasoning",
    show_default=True,
    type=click.Choice(["multimodal", "reasoning", "embedding"]),
    help="Module that is about to be loaded",
)
@with_appcontext
def vram_plan(module):
    """Show what would stay resident and what would be evicted — changes nothing.

    The same decision `ensure_vram_for()` makes: measured footprints, the
    prefill headroom and the least-recently-used order.
    """
    import os

    from app.resource_manager import get_resource_manager

    swap_url = os.getenv("LLAMA_SWAP_URL", "http://flai-llamaswap:8080").rstrip("/")
    rm = get_resource_manager()
    needed = rm.get_vram_needed_mb(module)
    loaded = rm._resident_modules(swap_url)  # noqa: SLF001 - read-only diagnosis
    plan = rm._eviction_plan(module, needed, loaded)  # noqa: SLF001
    mode = setting("VRAM_CO_RESIDENCY", "auto")

    click.echo(f"mode:      {mode}")
    click.echo(f"card:      {rm.hardware.total_vram_mb} MB total, {rm.hardware.available_vram_mb} MB free")
    click.echo(f"requested: {module}, {needed} MB")
    if not loaded:
        click.echo("resident:  none")
    for entry in loaded:
        mark = "evict" if entry["name"] in plan else "keep "
        click.echo(f"  {mark} {entry['name']}: {entry['mb']} MB (last used {entry['last_used']:.0f})")
    if not plan:
        click.echo("result:    loads next to the residents, nothing is unloaded")


@click.command("memory-report")
@click.option("--user", required=True, help="Login whose long-term memory is reported")
@click.option("--query", default="", help="Show what would be injected for this query")
@click.option("--limit", default=300, show_default=True, help="Facts to inspect")
@with_appcontext
def memory_report(user, query, limit):
    """Show what long-term memory holds for a user and what it would inject."""
    import collections

    from modules.base import BaseModule

    slm = current_app.modules.get("slm")
    if not slm or not slm.available:
        raise click.ClickException("SLM module is not available")

    facts = slm.list_facts(limit=limit, profile=user)
    kinds: collections.Counter = collections.Counter()
    for fact in facts:
        kinds[str(fact.get("kind") or (fact.get("metadata") or {}).get("kind") or "unclassified")] += 1

    click.echo(f"facts: {len(facts)} for {user}")
    for kind, count in kinds.most_common():
        click.echo(f"  {kind:14s} {count}")

    if query:
        raw = slm.recall(query, limit=int(current_app.config.get("SLM_RECALL_LIMIT", 7)) * 2, profile=user)
        selected = BaseModule.select_recalled_facts(
            raw,
            limit=int(current_app.config.get("SLM_RECALL_LIMIT", 7)),
            min_score=float(current_app.config.get("SLM_MIN_RECALL_SCORE", 0.5)),
        )
        chosen = {id(fact) for fact in selected}
        click.echo(f"recall for {query!r}: {len(raw)} candidates, {len(selected)} injected")
        for fact in raw:
            mark = "+" if id(fact) in chosen else "-"
            score = fact.get("score")
            click.echo(f"  {mark} {str(score if score is not None else '-')[:6]:>6}  {str(fact.get('content'))[:90]}")


@click.command("memory-purge")
@click.option("--user", required=True, help="Login whose facts are removed")
@click.option("--source", required=True, help="Only facts written by this source (e.g. auto_import)")
@click.option("--yes", is_flag=True, help="Actually delete; without it only lists")
@with_appcontext
def memory_purge(user, source, yes):
    """Delete facts of one source from a user's memory.

    The history import stored every user message verbatim, so a profile filled
    that way is mostly questions and task text. This removes exactly those rows
    and leaves everything the extractor distilled.
    """
    slm = current_app.modules.get("slm")
    if not slm or not slm.available:
        raise click.ClickException("SLM module is not available")

    facts = slm.list_facts(limit=1000, profile=user)
    meta_ids = slm.fact_meta_ids(profile=user, source=source) if hasattr(slm, "fact_meta_ids") else []
    targets = [f for f in facts if str(f.get("fact_id")) in set(meta_ids)]
    if not meta_ids:
        click.echo("no facts of that source recorded — nothing to do")
        return

    click.echo(f"{len(targets)} facts of source {source!r} for {user}:")
    for fact in targets[:20]:
        click.echo(f"  {str(fact.get('content'))[:80]}")
    if not yes:
        click.echo("dry run — pass --yes to delete")
        return

    removed = 0
    for fact in targets:
        if slm.delete_fact(str(fact.get("fact_id")), profile=user):
            removed += 1
    click.echo(f"deleted {removed} facts")


@click.command("memory-profile")
@click.option("--user", required=True, help="Login whose profile is consolidated")
@click.option("--lang", default="ru", type=click.Choice(["ru", "en"]), show_default=True)
@click.option("--limit", default=200, show_default=True, help="Facts to consider")
@click.option("--apply", is_flag=True, help="Store the result as the profile block")
@with_appcontext
def memory_profile(user, lang, limit, apply):
    """Rewrite the user's facts into one compact profile block.

    The block is what recall always injects, so it is the only place where
    duplicates and contradictions can be resolved once instead of on every
    request. Individual facts stay in the store.
    """
    from app.utils import format_prompt
    from modules.base import response_language_name

    slm = current_app.modules.get("slm")
    if not slm or not slm.available:
        raise click.ClickException("SLM module is not available")

    facts = slm.list_facts(limit=limit, profile=user)
    material = [
        fact
        for fact in facts
        # A previous block is an output, not evidence: consolidating it again
        # re-words the same sentences instead of rebuilding them from the facts,
        # and the model starts citing itself.
        if fact.get("source") != "profile_consolidation"
        and str(fact.get("kind") or "") in ("profile", "preference", "instruction", "project")
        and (fact.get("content") or fact.get("text"))
    ]
    if len(material) < 3:
        click.echo(f"only {len(material)} usable facts — nothing to consolidate yet")
        return

    lines = "\n".join(
        f"- [{str(fact.get('created_at') or '')[:10]}] {str(fact.get('content') or fact.get('text'))[:200]}"
        for fact in material
    )
    prompt = format_prompt(
        "memory_profile.template",
        {"facts": lines, "response_language": response_language_name(lang)},
        lang=lang,
    )
    if not prompt:
        raise click.ClickException("profile template failed to render")

    raw = current_app.modules["base"].call_llamacpp(
        [{"role": "user", "content": prompt}],
        model_type="multimodal",
        lang=lang,
        temperature=0.2,
    )
    answer = "\n".join(line.rstrip() for line in str(raw).splitlines() if line.strip())
    if not answer or answer.startswith("⚠"):
        raise click.ClickException(f"profile consolidation failed: {answer[:200]}")

    from app.memory_facts import split_profile_block

    current, closed = split_profile_block(answer)
    current = current[:1200]
    closed = closed[:1200]
    if not current:
        raise click.ClickException(f"profile consolidation returned no current part: {answer[:200]}")

    click.echo("CURRENT:")
    click.echo(current)
    if closed:
        click.echo("\nCLOSED:")
        click.echo(closed)
    click.echo(f"\n({len(current)} + {len(closed)} chars, from {len(material)} facts)")
    if not apply:
        click.echo("dry run — pass --apply to store it")
        return

    # A fact moved to CLOSED must stop being recalled on its own, or the model
    # gets "I live in Moscow" beside a block that says otherwise. The prompt
    # quotes the source fact verbatim for exactly this match.
    closed_lines = [line.lstrip("-• ").strip().lower() for line in closed.splitlines() if line.strip()]
    for old_fact in facts:
        text = str(old_fact.get("content") or old_fact.get("text") or "").strip().lower()
        if len(text) < 8 or old_fact.get("source") == "profile_consolidation":
            continue
        if any(text in line for line in closed_lines):
            fact_id = old_fact.get("fact_id")
            if fact_id:
                slm.delete_fact(fact_id, profile=user, reason="superseded")

    # The block is one fact, not a log: a previous one is retired before the new
    # one is written, otherwise every run leaves another block behind and recall
    # prepends several contradictory profiles at once.
    for old_fact in facts:
        if old_fact.get("source") != "profile_consolidation":
            continue
        fact_id = old_fact.get("fact_id")
        if fact_id:
            slm.delete_fact(fact_id, profile=user, reason="superseded")

    stored = slm.remember(
        current,
        metadata={"kind": "profile", "confidence": 1.0, "source": "profile_consolidation"},
        profile=user,
    )
    # History is a separate fact with its own kind: the always-injected block is
    # built from kind='profile' only, so a fact that stopped holding is kept but
    # is no longer asserted as current.
    kept_history = True
    if closed:
        kept_history = slm.remember(
            closed,
            metadata={"kind": "profile_history", "confidence": 1.0, "source": "profile_consolidation"},
            profile=user,
        )
    click.echo("stored" if stored and kept_history else "store failed")


def _message_text(content: str) -> str:
    """Plain text of a stored message, whether it is a string or parts."""
    import json

    body = (content or "").strip()
    if not body.startswith("["):
        return body
    try:
        parts = json.loads(body)
    except ValueError:
        return body
    return " ".join(
        str(part.get("text") or "") for part in parts if isinstance(part, dict) and part.get("type") == "text"
    ).strip()


@click.command("memory-extract-backfill")
@click.option("--user", required=True, help="Login whose messages are re-read")
@click.option("--limit", default=30, show_default=True, help="Messages to consider")
@click.option("--dry-run", is_flag=True, help="Report the yield without storing anything")
@with_appcontext
def memory_extract_backfill(user, limit, dry_run):
    """Run fact extraction over messages stored before extraction existed.

    The store fills from live messages only, so an installation whose history
    predates extraction has nothing for ``memory-profile`` to consolidate. This
    walks the most recent user messages and asks the extractor the same
    question the live path asks, with the same gates: a personal-looking
    message, the same strict parser, and a similarity check before storing.
    """
    from app.database import get_db
    from app.memory_facts import detect_language, looks_personal, parse_memory_facts
    from app.utils import format_prompt

    slm = current_app.modules.get("slm")
    if not slm or not slm.available:
        raise click.ClickException("SLM module is not available")
    base = current_app.modules.get("base")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT m.content,
                   (SELECT a.content FROM messages a
                     WHERE a.session_id = m.session_id AND a.role = 'assistant' AND a.id > m.id
                     ORDER BY a.id LIMIT 1) AS reply
            FROM messages m
            JOIN chat_sessions s ON s.id = m.session_id
            WHERE s.user_id = %s AND m.role = 'user'
            ORDER BY m.id DESC
            LIMIT %s
            """,
            (user, limit),
        )
        rows = cursor.fetchall()

    candidates = 0
    stored = 0
    for row in rows:
        text = _message_text(row.get("content") or "")
        if not text or not looks_personal(text):
            continue
        candidates += 1
        lang = detect_language(text)
        known = "\n".join(
            f"- {str(item.get('text') or item.get('content') or '')[:200]}"
            for item in slm.list_facts(limit=30, profile=user)[:30]
        )
        prompt = format_prompt(
            "memory_extract.template",
            {
                "known_facts": known or "-",
                "user_message": text,
                "assistant_message": str(row.get("reply") or "")[:1500],
            },
            lang=lang,
        )
        if not prompt:
            continue
        raw = base.call_llamacpp(
            [{"role": "user", "content": prompt}],
            model_type="multimodal",
            lang=lang,
            temperature=0.1,
        )
        facts = parse_memory_facts(str(raw))
        click.echo(f"  {text[:60]!r} -> {len(facts)} fact(s)")
        for fact in facts:
            try:
                if slm.check_similarity(fact["fact"], profile=user) >= 0.85:
                    continue
            except Exception:
                pass
            if dry_run:
                stored += 1
                continue
            if slm.remember(
                fact["fact"],
                metadata={"kind": fact["kind"], "confidence": fact["confidence"], "source": "llm_extract"},
                profile=user,
            ):
                stored += 1

    verdict = "would store" if dry_run else "stored"
    click.echo(f"\nmessages {len(rows)}, candidates {candidates}, facts {verdict}: {stored}")


@click.command("memory-eval")
@click.option("--user", required=True, help="Login whose memory is evaluated")
@click.option("--dataset", required=True, help="JSONL with {question, expected[]}")
@click.option("--k", default=5, show_default=True, help="Facts considered per question")
@click.option("--out", default="", help="Write the metrics to this JSON file")
@click.option("--no-profile", is_flag=True, help="Score recall alone, without the always-injected profile")
@with_appcontext
def memory_eval(user, dataset, k, out, no_profile):
    """Score recall against a question set: hit@k, MRR and the injection cost."""
    import json
    import time

    from modules.base import BaseModule

    slm = current_app.modules.get("slm")
    if not slm or not slm.available:
        raise click.ClickException("SLM module is not available")

    records = []
    with open(dataset, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    min_score = float(current_app.config.get("SLM_MIN_RECALL_SCORE", 0.5))

    def recall(question: str, top_k: int):
        started = time.time()
        try:
            raw = slm.recall(question, limit=max(top_k * 2, 6), profile=user)
        except Exception:
            raw = []
        selected = BaseModule.select_recalled_facts(raw, limit=top_k, min_score=min_score)
        if no_profile:
            # The profile block is always injected, so scoring it together with
            # recall saturates hit@k: every question "hits" the same block.
            selected = [fact for fact in selected if str(fact.get("kind") or "") != "profile"]
        return [str(fact.get("content") or fact.get("text") or "") for fact in selected], time.time() - started

    from app.memory_eval import evaluate_memory

    result = evaluate_memory(records, recall, k=k)
    click.echo(f"questions: {result['questions']}")
    click.echo(f"hit@{k}:    {result['hit']}")
    click.echo(f"MRR:       {result['mrr']}")
    click.echo(f"injected:  {result['avg_facts']} facts per question")
    click.echo(f"latency:   p50 {result['p50_ms']} ms")

    if out:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump({"user": user, "k": k, **result}, handle, ensure_ascii=False, indent=2)
        click.echo(f"written to {out}")


@click.command("rag-lemmatize-chunks")
@click.option("--user", required=True, help="Login whose mirrored chunks are lemmatized")
@click.option("--batch", default=200, show_default=True, help="Rows read per round")
@with_appcontext
def rag_lemmatize_chunks(user, batch):
    """Fill ``rag_chunks.lemmas`` for chunks mirrored before BM25 replaced ts_rank_cd."""
    from app.database import get_db
    from app.lemmatizer import lemmas as lemmatize

    updated = 0
    while True:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, text FROM rag_chunks WHERE user_id = %s AND lemmas IS NULL LIMIT %s",
                (user, batch),
            )
            rows = cursor.fetchall()
            if not rows:
                break
            for row in rows:
                cursor.execute(
                    "UPDATE rag_chunks SET lemmas = %s WHERE id = %s",
                    (lemmatize(row["text"] or ""), row["id"]),
                )
                updated += 1
    click.echo(f"Lemmatized {updated} chunks for {user}")


@click.command("rag-reindex")
@click.option("--user", required=True, help="Login whose documents are re-indexed")
@click.option("--doc", default="", help="Only this document id")
@with_appcontext
def rag_reindex(user, doc):
    """Re-index documents, e.g. after changing the chunking strategy."""
    from app.db import get_user_documents

    rag = current_app.modules.get("rag")
    if rag is None or not rag.check_availability():
        raise click.ClickException("RAG module or Qdrant is not available")

    documents = get_user_documents(user) or []
    if doc:
        documents = [row for row in documents if str(row.get("id")) == doc]
    if not documents:
        raise click.ClickException("No documents to re-index")

    folder = current_app.config.get("DOCUMENTS_FOLDER", "data/documents")
    done = failed = 0
    for row in documents:
        path = f"{folder}/{row.get('file_path')}"
        ok, message = rag.index_document(user, str(row["id"]), path)
        if ok:
            done += 1
            click.echo(f"  ok   {row.get('filename')} — {message}")
        else:
            failed += 1
            click.echo(f"  FAIL {row.get('filename')} — {message}")
    click.echo(f"\nRe-indexed {done} documents, {failed} failed")


@click.command("kernel-eval")
@click.option("--dataset", required=True, help="JSONL with {message, lang, expected_intent, expected_tools}")
@click.option("--mode", type=click.Choice(["router", "kernel", "both"]), default="both", show_default=True)
@click.option("--out", default="", help="Write the metrics to this JSON file")
@with_appcontext
def kernel_eval(dataset, mode, out):
    """Score the request kernel against the marker router on the same cases.

    Both modes call ``BaseModule.process_message``, so the comparison is between
    two decisions of the same function rather than two reimplementations. The
    acceptance gate is: the kernel is not worse on intent, reports at most two
    falsely selected tools on the seed set, and is not more than ten points
    worse on Russian than on English (the kernel prompt is English only).
    """
    import json

    from app.kernel_eval import load_dataset, run_evaluation, score, serialise

    cases = load_dataset(dataset)
    if not cases:
        raise click.ClickException(f"dataset {dataset} is empty")

    modes = ["router", "kernel"] if mode == "both" else [mode]
    payload = {}
    for name in modes:
        report = score(run_evaluation(current_app._get_current_object(), cases, name), cases)
        payload[name] = serialise(report)
        click.echo(f"\n=== {name} ===")
        click.echo(f"cases            {report['cases']}")
        click.echo(f"intent accuracy  {report['intent_accuracy']:.3f}")
        click.echo(f"tool exact       {report['tool_exact']:.3f}")
        click.echo(f"false tools      {report['false_tools']}")
        for lang, bucket in sorted(report["by_lang"].items()):
            click.echo(f"  {lang}: intent {bucket['intent_accuracy']:.3f} on {bucket['cases']} cases")
        if report["confusions"]:
            click.echo("  confusions (expected -> got):")
            for (expected, got), count in sorted(report["confusions"].items(), key=lambda item: -item[1]):
                click.echo(f"    {expected} -> {got}: {count}")

    if out:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        click.echo(f"\nwritten to {out}")


def _follow_graph_build(login: str) -> None:
    """Poll the build until no chunk is left. Ctrl-C stops following, not the build."""
    import time

    from app.db import kg_status

    stalled = 0
    previous = None
    while True:
        status = kg_status(login)
        progress = (status["chunks_done"], status["chunks_total"])
        stalled = stalled + 1 if progress == previous else 0
        previous = progress
        if stalled >= 120:
            click.echo("No progress for ten minutes; stop following and check `flask graph-status`.")
            return
        click.echo(
            f"chunks {status['chunks_done']}/{status['chunks_total']}  "
            f"nodes {status['nodes']}  edges {status['edges']}"
        )
        if not status["chunks_total"] or status["chunks_done"] >= status["chunks_total"]:
            return
        time.sleep(5)


@click.command("graph-build")
@click.option("--user", "login", required=True, help="Login whose documents are extracted")
@click.option("--doc", "doc_id", default="", help="Restrict the build to one document id")
@click.option("--rebuild", is_flag=True, help="Drop the existing graph rows of those documents and start over")
@click.option("--follow/--no-follow", default=True, show_default=True, help="Poll until every chunk is processed")
@click.option("--cancel", is_flag=True, help="Raise the interrupt flag so a running build stops")
@with_appcontext
def graph_build(login, doc_id, rebuild, follow, cancel):
    """Queue knowledge-graph extraction for a user's documents.

    The command only enqueues work: a CLI process does not share the worker's
    GPU lock, so calling the model here would run next to chat.
    """
    from app.graph_extract import queue_missing_documents

    queue = current_app.request_queue
    if cancel:
        queue.redis.set(queue.GRAPH_EXTRACT_INTERRUPT_KEY, "1", ex=300)
        click.echo("Graph extraction stopped; the running chunk returns to the queue.")
        return

    queued = queue_missing_documents(queue, login, doc_id=doc_id, rebuild=rebuild)
    try:
        from app.graph_docs import backfill_centroids, ensure_document_layer

        if backfill_centroids(current_app, login):
            click.echo("document centroids backfilled from the index")
        ensure_document_layer(login)
    except Exception as exc:  # noqa: BLE001 — the layer is a view, not the build
        click.echo(f"document layer not refreshed: {exc}")
    if not queued:
        click.echo(f"Nothing to do for {login}.")
    click.echo(f"queued {queued} document(s) for {login}")
    if follow and queued:
        _follow_graph_build(login)


@click.command("graph-status")
@click.option("--user", "login", required=True, help="Login whose build state is printed")
@with_appcontext
def graph_status(login):
    """Print the per-document build state and the totals."""
    from app.database import get_db
    from app.db import kg_status

    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT doc_id, status, chunks_done, chunks_total, error FROM kg_build_state"
            " WHERE user_id = %s ORDER BY doc_id",
            (login,),
        )
        rows = c.fetchall()

    for row in rows:
        error = f"  {row['error']}" if row["error"] else ""
        click.echo(f"{row['doc_id']}  {row['status']}  {row['chunks_done']}/{row['chunks_total']}{error}")

    status = kg_status(login)
    click.echo(
        f"{login}: documents {status['documents']}  chunks {status['chunks_done']}/{status['chunks_total']}  "
        f"nodes {status['nodes']}  edges {status['edges']}  ready={status['ready']}"
    )


@click.command("graph-merge-entities")
@click.option("--user", "login", required=True, help="Login whose duplicate nodes are merged")
@with_appcontext
def graph_merge_entities(login):
    """Merge graph nodes that name the same entity in different words.

    Extraction produced one person as three nodes while the matcher ignored word
    order and patronymics: the surname first, the given name first, and the same
    name with a patronymic. Two passes: identical lemma sets, then a name that
    extends a shorter one word by word. Occurrences and edges move to the longer
    name.
    """
    from app.database import get_db
    from app.db import merge_kg_nodes_sql

    def absorb(cursor, keeper, victim):
        merge_kg_nodes_sql(cursor, login, keeper["id"], victim["id"])

    merged = 0
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, kind, name, normalized, lemmas FROM kg_nodes WHERE user_id = %s",
            (login,),
        )
        nodes = [dict(row) for row in c.fetchall()]

        by_key: dict[tuple[str, str], dict] = {}
        for node in nodes:
            key = (node["kind"], " ".join(sorted(set(node["lemmas"] or []))))
            keeper = by_key.get(key)
            if keeper is None:
                by_key[key] = node
                continue
            longer = node if len(node["name"]) >= len(keeper["name"]) else keeper
            absorb(c, longer, keeper if longer is node else node)
            by_key[key] = longer
            merged += 1

        by_norm = {(node["kind"], node["normalized"]): node for node in by_key.values()}
        for node in sorted(by_key.values(), key=lambda item: len(str(item["normalized"]))):
            words = str(node["normalized"]).split()
            for cut in range(2, len(words)):
                parent = by_norm.get((node["kind"], " ".join(words[:cut])))
                if not parent or parent["id"] == node["id"]:
                    continue
                absorb(c, node, parent)
                node["lemmas"] = list(node["lemmas"] or []) + list(parent["lemmas"] or [])
                by_norm.pop((parent["kind"], parent["normalized"]), None)
                merged += 1

    click.echo(f"merged {merged} duplicate node(s) for {login}")


@click.command("graph-stats")
@click.option("--user", "login", required=True, help="Login whose extraction quality is reported")
@with_appcontext
def graph_stats(login):
    """Extraction quality without a labelled set.

    The proxies: how many relations the model proposed and how many survived the
    evidence check, how dense the graph is, how many entities stand alone, and
    which predicates dominate. They catch a regression in extraction the moment a
    build runs, with no human work.
    """
    from app.db import kg_extraction_stats

    stats = kg_extraction_stats(login)
    chunks = max(1, stats["chunks"])
    nodes = stats["nodes"]
    edges = stats["edges"]
    found = stats["found"]
    kept = stats["kept"]

    click.echo(f"user            {login}")
    click.echo(f"chunks          {stats['chunks']}")
    click.echo(f"entities        {stats['entities']}  ({stats['entities'] / chunks:.2f} per chunk)")
    click.echo(f"relations       {found} proposed, {kept} kept  ({100 * kept / found if found else 0:.0f} % survive)")
    click.echo(f"nodes           {nodes}")
    click.echo(f"edges           {edges}")
    click.echo(f"density         {edges / nodes if nodes else 0:.2f} edges per node")
    click.echo(f"isolated        {stats['isolated']}  ({100 * stats['isolated'] / nodes if nodes else 0:.0f} % of nodes)")
    if stats["predicates"]:
        click.echo("top predicates")
        for predicate, count in stats["predicates"]:
            click.echo(f"  {count:>4}  {predicate}")
