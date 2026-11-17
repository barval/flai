# tests/test_tasks_document_capability.py
"""The `document` capability writes and appends from inside a task step."""

import pytest

from app import db, documents_write

pytestmark = pytest.mark.requires_db

LOGIN = "task_document_test"


@pytest.fixture
def env(test_app, tmp_path, monkeypatch):
    root = tmp_path / "documents"
    monkeypatch.setattr(documents_write, "_documents_root", lambda: str(root))
    with test_app.app_context():
        from app.database import get_db

        def clear():
            with get_db() as conn:
                conn.cursor().execute("DELETE FROM documents WHERE user_id = %s", (LOGIN,))

        clear()
        test_app.config["DOCUMENTS_FOLDER"] = str(root)
        yield test_app
        clear()


def _step(**extra):
    return {"id": "s1", "tool": "document", "goal": "log the sighting", "extra": extra}


def test_a_step_writes_a_document(env):
    from app.tasks import runner

    with env.app_context():
        outcome = runner.run_direct_step(
            env, _step(name="peoples", content="09:00 kitchen", format="txt"),
            "", lambda: False, user_id=LOGIN, verify="off",
        )

    assert outcome["status"] == "passed"
    assert "peoples.txt" in outcome["result"]
    assert [d["filename"] for d in db.get_user_documents(LOGIN)] == ["peoples.txt"]


def test_two_steps_append_to_one_file(env):
    from app.tasks import runner

    with env.app_context():
        for line in ("first", "second"):
            runner.run_direct_step(
                env, _step(name="peoples", content=line, format="txt", append=True),
                "", lambda: False, user_id=LOGIN, verify="off",
            )
        docs = db.get_user_documents(LOGIN)

    assert len(docs) == 1
    path = f"{env.config['DOCUMENTS_FOLDER']}/{LOGIN}/{docs[0]['id']}.txt"
    with open(path, encoding="utf-8") as handle:
        assert handle.read().split() == ["first", "second"]


def test_a_refused_append_fails_the_step(env):
    from app.tasks import runner

    db.save_document(LOGIN, "upload-doc", "peoples.txt", 1, ".txt", f"{LOGIN}/upload-doc.txt")
    with env.app_context():
        outcome = runner.run_direct_step(
            env, _step(name="peoples", content="x", format="txt", append=True),
            "", lambda: False, user_id=LOGIN, verify="off",
        )

    assert outcome["status"] == "error"
    # A person reads this line in the task view: a sentence, not the code `append_target`.
    assert outcome["result"].startswith("⚠️ ")
    assert "загрузку" in outcome["result"]   # a localized sentence, not the code `append_target`
