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
@with_appcontext
def migrate_messages_format(dry_run):
    """Migrate old plain-text service messages to new JSON {prefix, text} format."""
    import json

    from app.database import get_db

    # Known prefixes for service messages (EN + RU)
    prefixes_whisper = ["🎤 Transcribed: ", "🎤 Распознано: "]
    prefixes_other = [
        "🎤 Transcribed: ",
        "🎤 Распознано: ",
        "Image edited from request: ",
        "Изображение отредактировано по запросу: ",
        "Image generated from request: ",
        "Изображение сгенерировано по запросу: ",
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

            # Already in JSON format — skip
            if content.startswith("{") and content.endswith("}"):
                try:
                    parsed = json.loads(content)
                    if "prefix" in parsed and "text" in parsed:
                        skipped += 1
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass

            # Choose prefix candidates based on model_name
            candidates = prefixes_whisper if model_name == "whisper" else prefixes_other

            matched = False
            for prefix in candidates:
                if content.startswith(prefix):
                    text = content[len(prefix) :]
                    new_content = json.dumps({"prefix": prefix, "text": text}, ensure_ascii=False)
                    click.echo(f"  MIGRATE id={msg_id}: {prefix!r} → {new_content[:80]}...")
                    if not dry_run:
                        c.execute("UPDATE messages SET content = %s WHERE id = %s", (new_content, msg_id))
                        conn.commit()
                    updated += 1
                    matched = True
                    break

            if not matched:
                skipped += 1

    click.echo(f"\nUpdated: {updated}, Skipped (already JSON or no match): {skipped}")
    if dry_run:
        click.echo("(dry-run, no changes made)")
