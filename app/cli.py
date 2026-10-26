import contextlib
import logging
import os

import click
from flask import current_app
from flask.cli import with_appcontext

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
@click.option("--model", type=click.Choice(["multimodal", "reasoning"]), default="multimodal", show_default=True)
@click.option("--scope-folder", default="", help="Restrict retrieval to one folder id")
@click.option("--scope-docs", default="", help="Comma-separated document ids to restrict retrieval to")
@click.option("--label", default="", help="Report label, e.g. 'dense baseline'")
@click.option("--out", default="", help="Report path (default: data/rag_eval/<timestamp>.json)")
@with_appcontext
def rag_eval(user, dataset, top_k, coverage, answers, model, scope_folder, scope_docs, label, out):
    """Measure retrieval quality (or end-to-end answer quality) on a fixed question set."""
    from datetime import datetime

    from app.rag_eval import evaluate, evaluate_answers, format_report, load_dataset, save_report

    rag = current_app.modules.get("rag")
    if rag is None:
        raise click.ClickException("RAG module is not available")
    if not rag.check_availability():
        raise click.ClickException("Qdrant is not reachable")

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

    def search(query: str, limit: int) -> list[dict]:
        chunks, _scores = rag.search(
            user, query, top_k=limit, file_coverage=coverage, doc_ids=scope_ids
        )
        return chunks

    config = {
        key: current_app.config.get(key)
        for key in ("RAG_TOP_K", "RAG_CHUNK_SIZE", "RAG_CHUNK_OVERLAP", "RAG_CHUNK_STRATEGY", "RAG_CONTEXT_PERCENT")
    }
    config["coverage"] = coverage
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

        report = evaluate_answers(items, answer_fn, search, top_k=top_k, label=run_label, config=config)
    else:
        report = evaluate(items, search, top_k=top_k, label=run_label, config=config)
    click.echo("")
    click.echo(format_report(report))

    target = out or f"data/rag_eval/{datetime.now():%Y%m%d-%H%M%S}.json"
    saved = save_report(report, target)
    click.echo(f"\nReport saved: {saved}")


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
    mode = os.getenv("VRAM_CO_RESIDENCY", "auto")

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
