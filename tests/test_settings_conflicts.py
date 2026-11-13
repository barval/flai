"""Where the admin panel and .env disagree, and whose value is running.

Precedence is database > environment, so a saved override always wins. The
disagreement is still worth a warning: an administrator who edits .env expects
it to apply, and nothing used to say that the panel value is the one in effect.
"""


def test_a_value_that_differs_from_env_is_reported(monkeypatch):
    from app import settings_store

    monkeypatch.setattr(settings_store, "load_overrides", lambda: {"MAX_CHAT_IMAGES": 2})
    monkeypatch.setenv("MAX_CHAT_IMAGES", "4")

    conflicts = settings_store.settings_conflicts()

    assert [item["key"] for item in conflicts] == ["MAX_CHAT_IMAGES"]
    assert conflicts[0]["panel"] == "2"
    assert conflicts[0]["env"] == "4"
    assert conflicts[0]["effective"] == "panel"


def test_equal_values_are_not_a_conflict(monkeypatch):
    from app import settings_store

    monkeypatch.setattr(settings_store, "load_overrides", lambda: {"MAX_CHAT_IMAGES": 4})
    monkeypatch.setenv("MAX_CHAT_IMAGES", "4")

    assert settings_store.settings_conflicts() == []


def test_a_key_that_env_does_not_define_is_not_a_conflict(monkeypatch):
    from app import settings_store

    monkeypatch.setattr(settings_store, "load_overrides", lambda: {"MAX_CHAT_IMAGES": 2})
    monkeypatch.delenv("MAX_CHAT_IMAGES", raising=False)

    assert settings_store.settings_conflicts() == []


def test_a_secret_is_masked_in_both_columns(monkeypatch):
    from app import settings_store

    key = next(iter(__import__("app.settings_registry", fromlist=["SECRET_KEYS"]).SECRET_KEYS))
    monkeypatch.setattr(settings_store, "load_overrides", lambda: {key: "panel-secret"})
    monkeypatch.setenv(key, "env-secret")

    conflicts = settings_store.settings_conflicts()

    assert conflicts and conflicts[0]["panel"] == "••••"
    assert conflicts[0]["env"] == "••••"


def test_boolean_spellings_are_the_same_value():
    from app.settings_store import comparable

    assert comparable(True) == comparable("1") == comparable("true")
    assert comparable(False) == comparable("0") == comparable("false")
    assert comparable(" TRUE ") == comparable(True)


def test_different_booleans_stay_different():
    from app.settings_store import comparable

    assert comparable(True) != comparable("0")
    assert comparable(False) != comparable("true")
