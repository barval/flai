"""Contract guard: every in-scope `/v1` endpoint must exist with its methods.

This mirrors the endpoint table in `docs/API.md`. It deliberately enumerates
routes instead of deriving them, so an accidental removal or rename fails the
build rather than silently breaking documented API clients.
"""

import pytest

EXPECTED_ENDPOINTS = {
    "/v1/models": {"GET", "HEAD", "OPTIONS"},
    "/v1/chat/completions": {"POST", "OPTIONS"},
    "/v1/embeddings": {"POST", "OPTIONS"},
    "/v1/audio/speech": {"POST", "OPTIONS"},
    "/v1/audio/transcriptions": {"POST", "OPTIONS"},
    "/v1/images/generations": {"POST", "OPTIONS"},
    "/v1/images/edits": {"POST", "OPTIONS"},
    "/v1/videos": {"POST", "OPTIONS"},
    "/v1/videos/<task_id>": {"GET", "HEAD", "OPTIONS"},
    "/v1/files": {"GET", "POST", "HEAD", "OPTIONS"},
    "/v1/files/<file_id>": {"GET", "DELETE", "HEAD", "OPTIONS"},
    "/v1/files/<file_id>/content": {"GET", "HEAD", "OPTIONS"},
    "/v1/flai/me": {"GET", "HEAD", "OPTIONS"},
    "/v1/flai/tasks": {"GET", "HEAD", "OPTIONS"},
    "/v1/flai/tasks/<task_id>": {"GET", "HEAD", "OPTIONS"},
    "/v1/flai/tasks/<task_id>/cancel": {"POST", "OPTIONS"},
    "/v1/flai/tasks/<task_id>/content": {"GET", "HEAD", "OPTIONS"},
    "/v1/flai/chat/async": {"POST", "OPTIONS"},
    "/v1/flai/documents": {"GET", "POST", "HEAD", "OPTIONS"},
    "/v1/flai/documents/<file_id>": {"GET", "DELETE", "HEAD", "OPTIONS"},
    "/v1/flai/documents/<file_id>/content": {"GET", "HEAD", "OPTIONS"},
    "/v1/flai/rlm": {"POST", "OPTIONS"},
    "/v1/flai/sessions": {"GET", "POST", "HEAD", "OPTIONS"},
    "/v1/flai/sessions/<session_id>/messages": {"GET", "HEAD", "OPTIONS"},
}


def _collect_v1_rules(test_app):
    return {rule.rule: rule.methods for rule in test_app.url_map.iter_rules() if rule.rule.startswith("/v1")}


@pytest.mark.unit
def test_every_documented_v1_endpoint_is_registered(test_app):
    rules = _collect_v1_rules(test_app)
    missing = sorted(set(EXPECTED_ENDPOINTS) - set(rules))
    assert not missing, f"Documented endpoints missing from the app: {missing}"
    unexpected = sorted(set(rules) - set(EXPECTED_ENDPOINTS) - {"/v1", "/v1/static/<path:filename>"})
    assert not unexpected, f"Undocumented /v1 endpoints registered: {unexpected}"
    for path, methods in EXPECTED_ENDPOINTS.items():
        assert methods <= rules[path], f"{path}: expected methods {methods}, got {rules[path]}"
