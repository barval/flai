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


@click.command("cleanup-uploads")
@click.option("--dry-run", is_flag=True, help="Only list orphans, do not delete")
@with_appcontext
def cleanup_uploads(dry_run):
    """Remove orphaned files from uploads/ not referenced in messages table."""
    from app.database import get_db

    upload_folder = current_app.config.get("UPLOAD_FOLDER", "data/uploads")
    if not os.path.isabs(upload_folder):
        upload_folder = os.path.join(current_app.root_path, "..", upload_folder)
    upload_folder = os.path.abspath(upload_folder)

    click.echo(f"Scanning {upload_folder} ...")

    # Collect all file paths referenced in DB
    db_files = set()
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT file_path FROM messages WHERE file_path IS NOT NULL")
        for row in c.fetchall():
            if row["file_path"]:
                db_files.add(row["file_path"])

    click.echo(f"Files referenced in DB: {len(db_files)}")

    # Walk upload dir and find orphans
    deleted_files = 0
    deleted_bytes = 0
    for session_dir in os.listdir(upload_folder):
        session_path = os.path.join(upload_folder, session_dir)
        if not os.path.isdir(session_path):
            continue
        for fname in os.listdir(session_path):
            fpath = os.path.join(session_dir, fname)
            full = os.path.join(upload_folder, fpath)
            if not os.path.isfile(full):
                continue
            if fpath not in db_files:
                fsize = os.path.getsize(full)
                click.echo(f"  ORPHAN {fpath} ({fsize} bytes)")
                if not dry_run:
                    os.remove(full)
                deleted_files += 1
                deleted_bytes += fsize

    # Remove empty directories
    empty_dirs = 0
    for session_dir in os.listdir(upload_folder):
        session_path = os.path.join(upload_folder, session_dir)
        if os.path.isdir(session_path) and not os.listdir(session_path):
            click.echo(f"  EMPTY DIR {session_dir}/")
            if not dry_run:
                os.rmdir(session_path)
            empty_dirs += 1

    click.echo(f"\nDeleted {deleted_files} orphan files ({deleted_bytes} bytes), {empty_dirs} empty dirs")
    if dry_run:
        click.echo("(dry-run, no changes made)")


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
