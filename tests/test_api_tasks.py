"""Owner-scoped API task registry, status and cancellation."""

import json
import time
from unittest.mock import Mock

import pytest

from app.api_tokens import create_api_token
from app.userdb import create_user


class FakeRedis:
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


@pytest.fixture
def task_tokens():
    create_user(login="taskowner", password="pw-taskowner-123456", name="Owner", language="en")
    create_user(login="otherowner", password="pw-otherowner-123456", name="Other", language="en")
    owner, _ = create_api_token("taskowner", name="tasks")
    other, _ = create_api_token("otherowner", name="tasks")
    return owner, other


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.unit
class TestApiTaskRegistry:
    def test_registration_uses_one_atomic_redis_script(self, task_redis, test_app, monkeypatch):
        from app.api_bridge import register_api_task

        calls = []
        original_eval = task_redis.eval

        def tracked_eval(*args):
            calls.append(args)
            return original_eval(*args)

        monkeypatch.setattr(task_redis, "eval", tracked_eval)
        with test_app.app_context():
            register_api_task({"login": "alice"}, "task-1", "s1", "/v1/videos", only_if_absent=True)

        assert len(calls) == 1
        assert calls[0][1:4] == (2, "api:task:task-1", "api:tasks:alice")
        assert calls[0][-1] == "1"

    def test_register_stores_owner_session_endpoint_and_ttl(self, task_redis, test_app):
        from app.api_bridge import register_api_task

        test_app.config["REDIS_RESULT_TTL"] = 1234
        with test_app.app_context():
            register_api_task({"login": "alice"}, "task-1", "session-1", "/v1/videos")

        assert task_redis.hashes["api:task:task-1"] == {
            "login": "alice",
            "session_id": "session-1",
            "endpoint": "/v1/videos",
            "created_at": task_redis.hashes["api:task:task-1"]["created_at"],
        }
        assert float(task_redis.hashes["api:task:task-1"]["created_at"]) > 0
        assert task_redis.sorted_sets["api:tasks:alice"] == {
            "task-1": float(task_redis.hashes["api:task:task-1"]["created_at"])
        }
        assert task_redis.expirations == {"api:task:task-1": 1234, "api:tasks:alice": 1234}

    def test_user_task_index_is_capped(self, task_redis, test_app):
        from app.api_bridge import API_TASK_INDEX_MAX, register_api_task

        with test_app.app_context():
            for index in range(API_TASK_INDEX_MAX + 2):
                register_api_task({"login": "alice"}, f"task-{index}", "s1", "/v1/videos")

        assert len(task_redis.sorted_sets["api:tasks:alice"]) == API_TASK_INDEX_MAX

    def test_unknown_task_has_no_owner(self, task_redis, test_app):
        from app.api_bridge import get_api_task_owner

        with test_app.app_context():
            assert get_api_task_owner("missing") is None

    def test_list_is_user_scoped_and_newest_first(self, task_redis, test_app):
        from app.api_bridge import list_api_tasks, register_api_task

        with test_app.app_context():
            register_api_task({"login": "alice"}, "old", "s1", "/v1/images")
            time.sleep(0.001)
            register_api_task({"login": "alice"}, "new", "s2", "/v1/videos")
            register_api_task({"login": "bob"}, "foreign", "s3", "/v1/other")
            assert [item["task_id"] for item in list_api_tasks("alice")] == ["new", "old"]

    def test_list_limit_is_bounded(self, task_redis, test_app):
        from app.api_bridge import list_api_tasks, register_api_task

        with test_app.app_context():
            for i in range(4):
                register_api_task({"login": "alice"}, f"task-{i}", "s1", "/v1/images")
            assert len(list_api_tasks("alice", limit=2)) == 2

    def test_requeue_registration_does_not_overwrite_foreign_owner(self, task_redis, test_app):
        from app.api_bridge import register_api_task

        with test_app.app_context():
            register_api_task({"login": "alice"}, "parent", "s1", "/v1/chat/completions")
            register_api_task({"login": "bob"}, "child", "s2", "/v1/videos")
            accepted = register_api_task({"login": "alice"}, "child", "s1", "/v1/chat/completions", only_if_absent=True)

        assert accepted is False
        assert task_redis.hashes["api:task:child"]["login"] == "bob"
        assert "child" not in task_redis.sorted_sets["api:tasks:alice"]
        assert task_redis.hashes["api:task:child"]["session_id"] == "s2"
        assert task_redis.hashes["api:task:child"]["endpoint"] == "/v1/videos"

    def test_requeue_registration_rejects_incomplete_existing_metadata(self, task_redis, test_app):
        from app.api_bridge import register_api_task

        task_redis.hashes["api:task:child"] = {"login": "alice"}
        with test_app.app_context():
            accepted = register_api_task({"login": "alice"}, "child", "s1", "/v1/chat/completions", only_if_absent=True)

        assert accepted is False
        assert task_redis.hashes["api:task:child"] == {"login": "alice"}

    def test_requeue_registration_accepts_complete_matching_metadata(self, task_redis, test_app):
        from app.api_bridge import register_api_task

        with test_app.app_context():
            register_api_task({"login": "alice"}, "child", "s1", "/v1/chat/completions")
            accepted = register_api_task({"login": "alice"}, "child", "s1", "/v1/chat/completions", only_if_absent=True)

        assert accepted is True
        assert task_redis.hashes["api:task:child"]["login"] == "alice"

    def test_requeue_child_must_not_be_registered_to_another_owner(self, task_redis, test_app, monkeypatch):
        from app.api_bridge import register_api_task

        with test_app.app_context():
            register_api_task({"login": "alice"}, "parent", "s1", "/v1/chat/completions")
            register_api_task({"login": "bob"}, "child", "s2", "/v1/videos")
        monkeypatch.setattr(
            test_app.request_queue,
            "check_result",
            lambda task_id: {
                "status": "completed",
                "result": {"status": "queued", "request_id": "child"},
            },
        )
        with test_app.app_context():
            from flask import g

            g.api_user = {"login": "alice", "language": "en"}
            from app.routes.api_v1 import _serialize_api_task

            result = _serialize_api_task(
                "parent",
                {"login": "alice", "session_id": "s1", "endpoint": "/v1/chat/completions", "created_at": "1"},
                {"status": "completed", "result": {"status": "queued", "request_id": "child"}},
            )
        assert result["id"] == "parent"
        assert result["status"] == "error"
        assert result["error"].startswith("⚠️ ")
        assert task_redis.hashes["api:task:child"]["login"] == "bob"


@pytest.mark.unit
class TestApiTaskRoutes:
    def test_list_returns_only_tasks_for_the_owner(self, test_app, task_tokens, task_redis, monkeypatch):
        from app.api_bridge import register_api_task

        owner, _other = task_tokens
        with test_app.app_context():
            register_api_task({"login": "taskowner"}, "own", "session-1", "/v1/images")
            register_api_task({"login": "otherowner"}, "foreign", "session-2", "/v1/videos")
        client = test_app.test_client()
        response = client.get("/v1/flai/tasks", headers=auth(owner))
        assert response.status_code == 200
        assert [item["id"] for item in response.get_json()["data"]] == ["own"]
        assert "foreign" not in response.get_data(as_text=True)

    def test_get_unknown_task_returns_404(self, test_app, task_tokens, task_redis):
        response = test_app.test_client().get("/v1/flai/tasks/unknown", headers=auth(task_tokens[0]))
        assert response.status_code == 404

    def test_foreign_owner_get_returns_404(self, test_app, task_tokens, task_redis):
        from app.api_bridge import register_api_task

        with test_app.app_context():
            register_api_task({"login": "otherowner"}, "foreign", "s2", "/v1/images")
        response = test_app.test_client().get("/v1/flai/tasks/foreign", headers=auth(task_tokens[0]))
        assert response.status_code == 404

    def test_status_pending_does_not_claim_queue_position(self, test_app, task_tokens, task_redis, monkeypatch):
        from app.api_bridge import register_api_task

        owner, _ = task_tokens
        with test_app.app_context():
            register_api_task({"login": "taskowner"}, "queued", "s1", "/v1/images")
        monkeypatch.setattr(test_app.request_queue, "check_result", lambda task_id: None)
        monkeypatch.setattr(
            test_app.request_queue,
            "get_user_requests_status",
            lambda login, lang: {"processing": None, "queued": [], "recent_completed": []},
        )
        response = test_app.test_client().get("/v1/flai/tasks/queued", headers=auth(owner))
        assert response.status_code == 200
        body = response.get_json()
        assert body["status"] == "queued"
        assert body["position"] is None

    def test_completed_result_is_sanitized(self, test_app, task_tokens, task_redis, monkeypatch):
        from app.api_bridge import register_api_task

        owner, _ = task_tokens
        with test_app.app_context():
            register_api_task({"login": "taskowner"}, "done", "s1", "/v1/images")
        monkeypatch.setattr(
            test_app.request_queue,
            "check_result",
            lambda task_id: {
                "status": "completed",
                "result": {
                    "response": "generated",
                    "file_path": "/private/path.png",
                    "file_data": "base64secret",
                    "prompt_tokens": 8,
                    "completion_tokens": 9,
                },
            },
        )
        response = test_app.test_client().get("/v1/flai/tasks/done", headers=auth(owner))
        assert response.status_code == 200
        assert response.get_json()["result"] == {
            "response": "generated",
            "usage": {"prompt_tokens": 8, "completion_tokens": 9},
        }
        assert "/private/" not in response.get_data(as_text=True)
        assert "base64secret" not in response.get_data(as_text=True)

    def test_unprefixed_queue_error_is_replaced_with_safe_message(self, test_app, task_tokens, task_redis, monkeypatch):
        from app.api_bridge import register_api_task

        owner, _ = task_tokens
        with test_app.app_context():
            register_api_task({"login": "taskowner"}, "failure", "s1", "/v1/videos")
        monkeypatch.setattr(
            test_app.request_queue,
            "check_result",
            lambda task_id: {
                "status": "error",
                "error": "private worker traceback",
                "result": {"error": "private worker traceback"},
            },
        )
        response = test_app.test_client().get("/v1/flai/tasks/failure", headers=auth(owner))
        body = response.get_json()
        assert body["status"] == "error"
        assert body["error"].startswith("⚠️ ")
        assert "private worker traceback" not in response.get_data(as_text=True)

    def test_handler_error_result_is_reported_as_error(self, test_app, task_tokens, task_redis, monkeypatch):
        from app.api_bridge import register_api_task

        owner, _ = task_tokens
        with test_app.app_context():
            register_api_task({"login": "taskowner"}, "failed", "s1", "/v1/audio/transcriptions")
        monkeypatch.setattr(
            test_app.request_queue,
            "check_result",
            lambda task_id: {
                "status": "completed",
                "result": {"is_error": True, "error": "⚠️ Transcription failed"},
            },
        )
        response = test_app.test_client().get("/v1/flai/tasks/failed", headers=auth(owner))
        assert response.status_code == 200
        assert response.get_json()["status"] == "error"
        assert response.get_json()["error"] == "⚠️ Transcription failed"

    def test_requeued_result_registers_and_exposes_child_task(self, test_app, task_tokens, task_redis, monkeypatch):
        from app.api_bridge import register_api_task

        owner, _ = task_tokens
        with test_app.app_context():
            register_api_task({"login": "taskowner"}, "parent", "s1", "/v1/chat/completions")
        monkeypatch.setattr(
            test_app.request_queue,
            "check_result",
            lambda task_id: (
                {
                    "status": "completed",
                    "result": {"status": "queued", "request_id": "child"},
                }
                if task_id == "parent"
                else None
            ),
        )
        monkeypatch.setattr(
            test_app.request_queue,
            "get_user_requests_status",
            lambda login, lang: {"processing": None, "queued": [], "recent_completed": []},
        )
        response = test_app.test_client().get("/v1/flai/tasks/parent", headers=auth(owner))
        assert response.status_code == 200
        body = response.get_json()
        assert body["id"] == "child"
        assert body["parent_task_id"] == "parent"
        assert body["status"] == "queued"
        assert task_redis.hashes["api:task:child"]["login"] == "taskowner"

    def test_list_deduplicates_requeued_child_tasks(self, test_app, task_tokens, task_redis, monkeypatch):
        from app.api_bridge import register_api_task

        owner, _ = task_tokens
        with test_app.app_context():
            register_api_task({"login": "taskowner"}, "parent", "s1", "/v1/chat/completions")
            register_api_task({"login": "taskowner"}, "child", "s1", "/v1/chat/completions")
        monkeypatch.setattr(
            test_app.request_queue,
            "check_result",
            lambda task_id: (
                {
                    "status": "completed",
                    "result": {"status": "queued", "request_id": "child"},
                }
                if task_id == "parent"
                else None
            ),
        )
        monkeypatch.setattr(
            test_app.request_queue,
            "get_user_requests_status",
            lambda login, lang: {"processing": None, "queued": [], "recent_completed": []},
        )
        response = test_app.test_client().get("/v1/flai/tasks", headers=auth(owner))
        ids = [item["id"] for item in response.get_json()["data"]]
        assert ids.count("child") == 1

    def test_foreign_owner_cancel_returns_404_without_queue_call(self, test_app, task_tokens, task_redis, monkeypatch):
        from app.api_bridge import register_api_task

        owner, _ = task_tokens
        with test_app.app_context():
            register_api_task({"login": "otherowner"}, "foreign", "s2", "/v1/videos")
        cancel = Mock(return_value=True)
        monkeypatch.setattr(test_app.request_queue, "cancel_task", cancel)
        response = test_app.test_client().post("/v1/flai/tasks/foreign/cancel", headers=auth(owner))
        assert response.status_code == 404
        cancel.assert_not_called()

    def test_processing_task_can_be_cancelled(self, test_app, task_tokens, task_redis, monkeypatch):
        from app.api_bridge import register_api_task

        owner, _ = task_tokens
        with test_app.app_context():
            register_api_task({"login": "taskowner"}, "active", "s1", "/v1/videos")
        monkeypatch.setattr(test_app.request_queue, "check_result", lambda task_id: None)
        monkeypatch.setattr(
            test_app.request_queue,
            "get_user_requests_status",
            lambda login, lang: {"processing": {"id": "active"}, "queued": [], "recent_completed": []},
        )
        monkeypatch.setattr(test_app.request_queue, "cancel_task", lambda task_id: True)
        response = test_app.test_client().post("/v1/flai/tasks/active/cancel", headers=auth(owner))
        assert response.status_code == 200
        assert response.get_json() == {"status": "cancelling", "task_id": "active"}

    def test_queued_task_is_not_claimed_cancelled(self, test_app, task_tokens, task_redis, monkeypatch):
        from app.api_bridge import register_api_task

        owner, _ = task_tokens
        with test_app.app_context():
            register_api_task({"login": "taskowner"}, "queued", "s1", "/v1/images")
        monkeypatch.setattr(test_app.request_queue, "check_result", lambda task_id: None)
        monkeypatch.setattr(test_app.request_queue, "cancel_task", lambda task_id: True)
        monkeypatch.setattr(
            test_app.request_queue,
            "get_user_requests_status",
            lambda login, lang: {"processing": None, "queued": [{"id": "queued"}], "recent_completed": []},
        )
        monkeypatch.setattr(test_app.request_queue.redis, "hexists", lambda *a: False)
        response = test_app.test_client().post("/v1/flai/tasks/queued/cancel", headers=auth(owner))
        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "task_not_cancellable"

    def test_terminal_task_is_not_cancellable(self, test_app, task_tokens, task_redis, monkeypatch):
        from app.api_bridge import register_api_task

        owner, _ = task_tokens
        with test_app.app_context():
            register_api_task({"login": "taskowner"}, "done", "s1", "/v1/images")
        monkeypatch.setattr(test_app.request_queue, "check_result", lambda task_id: {"status": "completed"})
        response = test_app.test_client().post("/v1/flai/tasks/done/cancel", headers=auth(owner))
        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "task_not_cancellable"

    def test_completion_race_does_not_report_cancelling(self, test_app, task_tokens, task_redis, monkeypatch):
        from app.api_bridge import register_api_task

        owner, _ = task_tokens
        with test_app.app_context():
            register_api_task({"login": "taskowner"}, "raced", "s1", "/v1/videos")
        results = iter((None, {"status": "completed", "result": {"response": "done"}}))
        monkeypatch.setattr(test_app.request_queue, "check_result", lambda task_id: next(results))
        monkeypatch.setattr(
            test_app.request_queue,
            "get_user_requests_status",
            lambda login, lang: {"processing": {"id": "raced"}, "queued": [], "recent_completed": []},
        )
        monkeypatch.setattr(test_app.request_queue, "cancel_task", lambda task_id: True)
        response = test_app.test_client().post("/v1/flai/tasks/raced/cancel", headers=auth(owner))
        assert response.status_code == 409

    def test_cancel_unknown_task_returns_404(self, test_app, task_tokens, task_redis):
        response = test_app.test_client().post("/v1/flai/tasks/unknown/cancel", headers=auth(task_tokens[0]))
        assert response.status_code == 404


@pytest.mark.unit
def test_task_result_sanitizer_never_returns_paths_tokens_or_file_payloads():
    from app.api_bridge import sanitize_api_task_result

    result = sanitize_api_task_result(
        {
            "response": "done",
            "file_path": "/private/file.png",
            "file_data": "base64secret",
            "api_token": "rawtoken",
            "usage": {"prompt_tokens": 1},
        }
    )
    assert result == {"response": "done", "usage": {"prompt_tokens": 1}}
    assert "/private/" not in json.dumps(result)
    assert "base64secret" not in json.dumps(result)
    assert "rawtoken" not in json.dumps(result)
