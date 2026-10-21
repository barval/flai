"""CLI command tests: `flask backfill-attachment-paths` migrates legacy
content-JSON base64 attachment parts to saved disk files with plain paths."""

import json as _json
import os
from base64 import b64encode

from tests.test_multi_attachment import PNG_1PX_RAW


class TestBackfillAttachmentPaths:
    def _msg(self, test_app, content, file_path=None, file_data=None):
        from app import db

        with test_app.app_context():
            session_id = db.create_session("clitest", title="backfill")
            msg_id = db.save_message(
                session_id,
                "user",
                content,
                file_data=file_data,
                file_path=file_path,
            )
            return session_id, msg_id

    def test_backfill_saves_pathless_parts_and_clears_column_base64(self, runner, test_app):
        from app import db

        first_payload = b64encode(PNG_1PX_RAW).decode("ascii")
        second_payload = b64encode(b"second-bytes").decode("ascii")
        content = _json.dumps(
            [
                {"type": "text", "text": "q"},
                {"type": "image", "file_data": first_payload, "file_type": "image/png", "file_name": "a.png"},
                {"type": "image", "file_data": second_payload, "file_type": "image/png", "file_name": "b.png"},
            ]
        )
        session_id, _msg_id = self._msg(test_app, content, file_data=first_payload)

        result = runner.invoke(args=["backfill-attachment-paths"])
        assert result.exit_code == 0, result.output
        assert "Updated: 1" in result.output

        with test_app.app_context():
            msgs = db.get_session_messages(session_id)
        parts = _json.loads(msgs[0]["content"])
        images = [p for p in parts if p.get("type") == "image"]
        assert len(images) == 2
        # First part reuses the row-level path (column file_path), second is
        # saved to disk by the migration.
        for p in images:
            assert p.get("file_path"), "every part must carry a path after the migration"
            assert "file_data" not in p, "no base64 payload may remain in content"
            assert os.path.exists(os.path.join(test_app.config["UPLOAD_FOLDER"], p["file_path"]))

    def test_backfill_is_idempotent(self, runner, test_app):
        from app import db

        payload = b64encode(PNG_1PX_RAW).decode("ascii")
        content = _json.dumps(
            [
                {"type": "text", "text": "q"},
                {"type": "image", "file_data": payload, "file_type": "image/png", "file_name": "a.png"},
            ]
        )
        session_id, _msg_id = self._msg(test_app, content, file_data=payload)

        first = runner.invoke(args=["backfill-attachment-paths"])
        assert "Updated: 1" in first.output
        second = runner.invoke(args=["backfill-attachment-paths"])
        assert second.exit_code == 0
        assert "Updated: 0" in second.output

        with test_app.app_context():
            msgs = db.get_session_messages(session_id)
        parts = _json.loads(msgs[0]["content"])
        images = [p for p in parts if p.get("type") == "image"]
        assert len(images) == 1
        assert images[0].get("file_path")
        assert "file_data" not in images[0]

    def test_backfill_dry_run_changes_nothing(self, runner, test_app):
        from app import db

        payload = b64encode(PNG_1PX_RAW).decode("ascii")
        content = _json.dumps(
            [
                {"type": "text", "text": "q"},
                {"type": "image", "file_data": payload, "file_type": "image/png", "file_name": "a.png"},
            ]
        )
        session_id, _msg_id = self._msg(test_app, content, file_data=None)
        from app import db as _db

        with test_app.app_context():
            raw = _db.get_session_messages(session_id)
        parts = _json.loads(raw[0]["content"])
        assert "file_data" in parts[1], "sanity: pre-migration part keeps its payload"

        result = runner.invoke(args=["backfill-attachment-paths", "--dry-run"])
        assert result.exit_code == 0
        assert "(dry-run, no changes made)" in result.output

        with test_app.app_context():
            msgs = db.get_session_messages(session_id)
        parts = _json.loads(msgs[0]["content"])
        images = [p for p in parts if p.get("type") == "image"]
        assert len(images) == 1
        assert "file_data" in images[0], "dry-run must leave the payload in place"
        # Dry-run is report-only: no file may be written to disk.
        saved = [
            p
            for p in images
            if p.get("file_path") and os.path.exists(os.path.join(test_app.config["UPLOAD_FOLDER"], p["file_path"]))
        ]
        assert not saved, "dry-run must not write any attachment file"


class TestCleanupUploadsTaskMedia:
    """cleanup-uploads must keep task-generated files (task_steps.extra.media)
    referenced by running/completed tasks (whole-branch review, I6)."""

    def _setup_upload(self, test_app, rel_path):
        full = os.path.join(test_app.config["UPLOAD_FOLDER"], rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(b"media-bytes")

    def test_task_media_survives_cleanup(self, runner, test_app):
        from app.database import get_db

        with test_app.app_context():
            from app.tasks import store

            task_id = store.create_task("clitest", 3, "T", "ru", "neutral")
            step_id = store.add_step(task_id=task_id, seq=1, kind="subtask", goal="draw", tool="image_gen")
            store.update_step(
                step_id,
                extra={"subtask_queue_id": "q1", "media": {"file_path": "clitest/task-image.png"}},
            )

        self._setup_upload(test_app, "clitest/task-image.png")
        self._setup_upload(test_app, "clitest/orphan.png")

        result = runner.invoke(args=["cleanup-uploads"])

        assert result.exit_code == 0, result.output
        # The task media file survives; the true orphan is deleted.
        assert os.path.exists(os.path.join(test_app.config["UPLOAD_FOLDER"], "clitest/task-image.png"))
        assert not os.path.exists(os.path.join(test_app.config["UPLOAD_FOLDER"], "clitest/orphan.png"))
        with test_app.app_context(), get_db() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM task_steps")
            c.execute("DELETE FROM tasks")


def test_storage_by_owner_sums_owned_dirs_and_skips_deleted(tmp_path):
    """Quota recalculation counts only files that remain in owned directories."""
    from app.cli import _storage_by_owner

    owned = tmp_path / "aaaa"
    owned.mkdir()
    (owned / "keep.png").write_bytes(b"x" * 10)
    (owned / "gone.png").write_bytes(b"y" * 5)
    unknown = tmp_path / "bbbb"
    unknown.mkdir()
    (unknown / "orphan.png").write_bytes(b"z" * 3)

    usage, unknown_dirs, unknown_bytes = _storage_by_owner(
        str(tmp_path), {"aaaa": "alice"}, exclude={"aaaa/gone.png"}
    )

    assert usage == {"alice": 10}
    assert unknown_dirs == 1
    assert unknown_bytes == 3
