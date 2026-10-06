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
        session_id, _msg_id = self._msg(test_app, content, file_data=payload)

        result = runner.invoke(args=["backfill-attachment-paths", "--dry-run"])
        assert result.exit_code == 0
        assert "(dry-run, no changes made)" in result.output

        with test_app.app_context():
            msgs = db.get_session_messages(session_id)
        parts = _json.loads(msgs[0]["content"])
        images = [p for p in parts if p.get("type") == "image"]
        assert len(images) == 1
        assert "file_data" in images[0], "dry-run must leave the payload in place"
