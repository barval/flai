# tests/test_scheduler_tools.py
"""Scheduling from chat: the model names the time, the server checks it.

A wrong date is the one mistake the user cannot see until the wrong moment, so the tool
answers with the concrete time it stored. Everything here is deterministic: the phrase is
turned into a moment, and the moment is refused or accepted on its own merits.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import scheduler_store, userdb

pytestmark = pytest.mark.requires_db

LOGIN = "scheduler_tools_test"
ZONE = "Europe/Moscow"


@pytest.fixture
def env(test_app, monkeypatch):
    with test_app.app_context():
        from app.database import get_db, init_db

        init_db()

        def clear():
            with get_db() as conn:
                c = conn.cursor()
                c.execute("DELETE FROM tasks WHERE user_id = %s", (LOGIN,))
                c.execute("DELETE FROM scheduler_items WHERE user_id = %s", (LOGIN,))
                c.execute("DELETE FROM users WHERE login = %s", (LOGIN,))

        clear()
        userdb.create_user(LOGIN, "pw", "Name", language="ru")
        monkeypatch.setitem(test_app.config, "TIMEZONE_STR", ZONE)
        yield test_app
        clear()


def _ctx(**over):
    ctx = {"user_id": LOGIN, "lang": "ru", "message_text": ""}
    ctx.update(over)
    return ctx


def _local_now() -> datetime:
    return datetime.now(UTC).astimezone(ZoneInfo(ZONE))


def _tomorrow_at(nine=False) -> str:
    moment = _local_now() + timedelta(days=1)
    return moment.strftime("%Y-%m-%d 09:00" if nine else "%Y-%m-%d %H:%M")


def test_a_wall_clock_time_is_read_as_the_installation_zone(env):
    from app.scheduler_tools import parse_when

    parsed = parse_when("2026-11-18 09:00")

    assert parsed == datetime(2026, 11, 18, 9, 0)


def test_a_german_style_date_is_read_too(env):
    from app.scheduler_tools import parse_when

    assert parse_when("18.11.2026 09:00") == datetime(2026, 11, 18, 9, 0)


def test_nonsense_is_not_a_time(env):
    from app.scheduler_tools import parse_when

    assert parse_when("завтра утром") is None
    assert parse_when("") is None


def test_a_reminder_is_stored_in_the_installation_zone(env):
    from app.tools import execute_tool

    answer = execute_tool("schedule_reminder", {"when": _tomorrow_at(nine=True), "text": "🔔 Отчёт — {when}"}, _ctx())

    assert "⚠️" not in answer
    items = scheduler_store.list_items(LOGIN)
    assert len(items) == 1
    item = items[0]
    assert item["kind"] == "reminder"
    assert item["timezone"] == ZONE
    assert item["time_of_day"].hour == 9
    assert item["repeat"] == "once"
    assert item["next_run_at"] is not None
    # The answer names the moment, which is the only chance the user has to notice a
    # misunderstanding before it fires.
    assert "09:00" in answer


def test_an_action_carries_the_goal_and_its_title(env):
    from app.tools import execute_tool

    execute_tool("schedule_action", {"when": _tomorrow_at(), "goal": "Проверь все комнаты", "title": "Комнаты"}, _ctx())

    item = scheduler_store.list_items(LOGIN)[0]
    assert item["kind"] == "action"
    assert item["body"] == "Проверь все комнаты"
    assert item["title"] == "Комнаты"


def test_a_past_one_off_is_refused_with_a_question(env):
    from app.tools import execute_tool

    past = (_local_now() - timedelta(days=2)).strftime("%Y-%m-%d 09:00")

    answer = execute_tool("schedule_reminder", {"when": past, "text": "🔔 Старое"}, _ctx())

    assert answer.startswith("⚠️ ")
    assert "?" in answer
    assert scheduler_store.list_items(LOGIN) == []


def test_a_past_repeating_phrase_moves_to_its_next_occurrence(env):
    """«Every day at nine» said at ten means tomorrow at nine, not a refusal."""
    from app.tools import execute_tool

    earlier = (_local_now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")

    answer = execute_tool(
        "schedule_reminder", {"when": earlier, "text": "🔔 Дневник — {when}", "repeat": "daily"}, _ctx()
    )

    assert "⚠️" not in answer
    item = scheduler_store.list_items(LOGIN)[0]
    assert item["repeat"] == "daily"
    assert item["next_run_at"] > datetime.now(UTC) + timedelta(minutes=30)


def test_an_unclear_time_asks_rather_than_guessing(env):
    from app.tools import execute_tool

    answer = execute_tool("schedule_reminder", {"when": "завтра утром", "text": "🔔"}, _ctx())

    assert answer.startswith("⚠️ ")
    assert scheduler_store.list_items(LOGIN) == []


def test_an_empty_reminder_is_refused(env):
    from app.tools import execute_tool

    answer = execute_tool("schedule_reminder", {"when": _tomorrow_at(), "text": "  "}, _ctx())

    assert answer.startswith("⚠️ ")
    assert scheduler_store.list_items(LOGIN) == []


def test_the_kernel_sends_scheduling_to_the_chat_loop(env):
    from app.kernel import INTENT_ACTIONS, KernelDecision, SourceFlags, decision_to_route

    assert INTENT_ACTIONS["schedule"] == "none"
    decision = KernelDecision(
        intent="schedule", query="напомни завтра в 9", sources=SourceFlags(), tools=None, confidence=1.0
    )
    route = decision_to_route(decision, "напомни завтра в 9")

    assert route["action"] == "none"
    assert route["query"] == "напомни завтра в 9"


def test_the_marker_router_has_a_scheduling_category(env):
    from unittest.mock import MagicMock

    from modules.base import BaseModule

    module = BaseModule.__new__(BaseModule)
    module.logger = MagicMock()
    message = "напомни завтра в 9 утра про отчёт"

    result = module._parse_router_response(f"[-SCHEDULE-] {message}", message, "2026-11-18 09:00", "ru")

    assert result["action"] == "none"
    assert result["query"] == message


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_both_prompts_name_the_intent(env, lang):
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    kernel = (root / "prompts/en/kernel.template").read_text(encoding="utf-8")
    marker = (root / f"prompts/{lang}/base_text.template").read_text(encoding="utf-8")

    assert "schedule_reminder" in kernel
    assert "schedule" in kernel.lower()
    assert "[-SCHEDULE-]" in marker


def test_a_queued_task_is_cancelled_when_its_item_is_deleted(env, client):
    from app.tasks import store

    with client.session_transaction() as browser_session:
        browser_session["login"] = LOGIN
        browser_session["user_id"] = LOGIN
        browser_session["name"] = "Name"

    item_id = scheduler_store.create_item(
        LOGIN, kind="action", title="Rooms", body="Check", repeat="once", at=datetime.now(UTC) + timedelta(hours=1),
        timezone_name=ZONE,
    )
    task_id = store.create_task(
        LOGIN, 5, "Rooms", "ru", "neutral", scheduler_item_id=item_id, scheduler_deliver_at=datetime.now(UTC)
    )

    response = client.post(f"/api/scheduler/items/{item_id}/delete")

    assert response.status_code == 200
    assert scheduler_store.get_item(item_id) is None
    task = store.get_task(task_id)
    assert task["status"] == "cancelled"
    assert task["scheduler_delivered_at"] is not None


def test_the_list_is_owner_scoped(env, client):
    scheduler_store.create_item(
        LOGIN, kind="reminder", title="Mine", body="🔔", repeat="once", at=datetime.now(UTC) + timedelta(hours=1),
        timezone_name=ZONE,
    )

    unauthenticated = client.get("/api/scheduler/items")
    assert unauthenticated.status_code == 401

    with client.session_transaction() as browser_session:
        browser_session["login"] = LOGIN
        browser_session["user_id"] = LOGIN
        browser_session["name"] = "Name"
    listed = client.get("/api/scheduler/items").get_json()

    assert [item["title"] for item in listed["items"]] == ["Mine"]
    assert listed["items"][0]["kind"] == "reminder"


def test_a_relative_request_needs_no_date(env):
    """«In two minutes» must not become date arithmetic — a model invents dates."""
    from datetime import UTC, datetime

    from app.tools import execute_tool

    answer = execute_tool("schedule_reminder", {"in_minutes": 2, "text": "🔔 Проверить"}, _ctx())

    assert "⚠️" not in answer
    item = scheduler_store.list_items(LOGIN)[0]
    delta = item["next_run_at"] - datetime.now(UTC)
    assert 60 < delta.total_seconds() < 180


def test_an_absolute_moment_still_works(env):
    from app.tools import execute_tool

    answer = execute_tool("schedule_reminder", {"when": _tomorrow_at(nine=True), "text": "🔔 Отчёт"}, _ctx())

    assert "⚠️" not in answer
    assert scheduler_store.list_items(LOGIN)[0]["time_of_day"].hour == 9


def test_no_time_at_all_is_refused(env):
    from app.tools import execute_tool

    answer = execute_tool("schedule_reminder", {"text": "🔔 Без времени"}, _ctx())

    assert answer.startswith("⚠️ ")
    assert scheduler_store.list_items(LOGIN) == []


def test_a_nonsense_interval_is_refused(env):
    from app.tools import execute_tool

    for value in (0, -5, "soon", 10**9):
        answer = execute_tool("schedule_reminder", {"in_minutes": value, "text": "🔔"}, _ctx())
        assert answer.startswith("⚠️ "), value
    assert scheduler_store.list_items(LOGIN) == []


def test_a_bare_time_means_today_or_tomorrow(env):
    """«Remind me at 13:23» names no date, and the model must not invent one."""
    from datetime import UTC, datetime, timedelta
    from zoneinfo import ZoneInfo

    from app.tools import execute_tool

    zone = ZoneInfo(ZONE)
    now_local = datetime.now(UTC).astimezone(zone)
    ahead = (now_local + timedelta(minutes=40)).strftime("%H:%M")
    passed = (now_local - timedelta(minutes=40)).strftime("%H:%M")

    execute_tool("schedule_reminder", {"when": ahead, "text": "🔔 Скоро"}, _ctx())
    items = scheduler_store.list_items(LOGIN)
    assert len(items) == 1
    first = items[0]["next_run_at"]
    assert first > datetime.now(UTC) + timedelta(minutes=5)

    execute_tool("schedule_reminder", {"when": passed, "text": "🔔 Позже"}, _ctx())
    later = [i for i in scheduler_store.list_items(LOGIN) if i["next_run_at"] != first][0]
    assert later["next_run_at"] > first


def test_the_chat_prompt_carries_the_clock(env):
    """Without it the model guessed a date once: 31 January for a request in November."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for name in ("chat.template", "chat_simple.template"):
        for lang in ("ru", "en"):
            text = (root / f"prompts/{lang}/{name}").read_text(encoding="utf-8")
            assert "{current_time_str}" in text, (lang, name)


def test_an_action_is_refused_below_the_hardware_floor(env):
    """Ten minutes silently becoming fifteen is worse than being told the floor."""
    env.config["SCHEDULER_ACTION_MIN_MINUTES"] = 5
    from app.tools import execute_tool

    refused = execute_tool(
        "schedule_action", {"when": _tomorrow_at(), "goal": "Проверь курс", "repeat": "minutes", "every_minutes": 2}, _ctx()
    )
    assert refused.startswith("⚠️ ")
    assert "5" in refused
    assert scheduler_store.list_items(LOGIN) == []

    accepted = execute_tool(
        "schedule_action", {"when": _tomorrow_at(), "goal": "Проверь курс", "repeat": "minutes", "every_minutes": 10}, _ctx()
    )
    assert "⚠️" not in accepted
    assert "10" in accepted  # the answer names the interval, not only the first firing
    assert scheduler_store.list_items(LOGIN)[0]["every_minutes"] == 10


def test_the_floor_is_fifteen_on_a_cpu_host(env):
    env.config["SCHEDULER_ACTION_MIN_MINUTES"] = 15
    from app.tools import execute_tool

    refused = execute_tool(
        "schedule_action", {"when": _tomorrow_at(), "goal": "Проверь курс", "repeat": "minutes", "every_minutes": 10}, _ctx()
    )

    assert refused.startswith("⚠️ ")
    assert "15" in refused


def test_the_defaults_follow_the_platform(env):
    """A GPU host allows five minutes; a CPU-only one gets fifteen."""
    from app.config import resolve_action_floor

    assert resolve_action_floor("nvidia", 0) == 5
    assert resolve_action_floor("amd", 0) == 5
    assert resolve_action_floor("cpu", 0) == 15
    assert resolve_action_floor("", 0) == 5
    # An explicit value wins over the hardware.
    assert resolve_action_floor("nvidia", 30) == 30
    assert resolve_action_floor("cpu", 7) == 7


def test_a_repeating_reminder_is_bounded_by_fifteen(env):
    env.config["SCHEDULER_ACTION_MIN_MINUTES"] = 5
    from app.tools import execute_tool

    refused = execute_tool(
        "schedule_reminder", {"when": _tomorrow_at(), "text": "🔔", "repeat": "minutes", "every_minutes": 5}, _ctx()
    )

    assert refused.startswith("⚠️ ")
    assert "15" in refused


def test_every_n_minutes_needs_no_first_moment(env):
    """«Every five minutes» names no start: the first firing is one interval away."""
    from datetime import UTC, datetime

    from app.tools import execute_tool

    env.config["SCHEDULER_ACTION_MIN_MINUTES"] = 5
    answer = execute_tool(
        "schedule_action", {"goal": "Курс доллара", "repeat": "minutes", "every_minutes": 5}, _ctx()
    )

    assert "⚠️" not in answer
    item = scheduler_store.list_items(LOGIN)[0]
    delta = item["next_run_at"] - datetime.now(UTC)
    assert 3 * 60 < delta.total_seconds() < 7 * 60


def test_the_floor_applies_to_every_way_of_naming_the_moment(env):
    """A date, a bare time or "in five minutes" must all hit the same floor."""
    from app.tools import execute_tool

    env.config["SCHEDULER_ACTION_MIN_MINUTES"] = 15
    for moment in (
        {"when": _tomorrow_at()},
        {"when": "23:59"},
        {"in_minutes": 5},
        {},
    ):
        answer = execute_tool(
            "schedule_action",
            {"goal": "Курс", "repeat": "minutes", "every_minutes": 5, **moment},
            _ctx(),
        )
        assert answer.startswith("⚠️ "), moment
        assert "15" in answer, moment
    assert scheduler_store.list_items(LOGIN) == []


def test_the_action_description_does_not_put_a_file_in_the_goal(env):
    """Measured: an example ending in "save it to a file" came back as a goal that wrote a CSV."""
    from app.tools import _SCHEDULER_DEFINITIONS

    action = next(d for d in _SCHEDULER_DEFINITIONS if d["function"]["name"] == "schedule_action")
    description = action["function"]["description"].lower()

    # The prohibition may name a file; the example must not model one.
    assert "save it to a file" not in description
    assert "nothing else" in description


def test_start_task_creates_a_prefixed_task(env):
    from app.tasks import store as task_store
    from app.tools import execute_tool

    answer = execute_tool(
        "start_task",
        {"goal": "Снимки всех комнат и события в файл", "title": "Снимки комнат"},
        _ctx(),
    )

    assert "⚠️" not in answer
    task = task_store.get_tasks_by_user(LOGIN)[0]
    assert task["title"] == "🛠️ Планировщик: Снимки комнат"
    assert "Снимки комнат" in answer


def test_start_task_refuses_an_empty_goal(env):
    from app.tools import execute_tool

    answer = execute_tool("start_task", {"goal": "  ", "title": "Пусто"}, _ctx())

    assert answer.startswith("⚠️ ")


def test_chat_does_not_offer_start_task(env):
    from app.tools import get_tool_definitions

    names = {d["function"]["name"] for d in get_tool_definitions()}

    assert "start_task" not in names


def test_a_run_does_not_offer_scheduling_or_the_skill_loader(env):
    from app.tools import scheduler_run_tool_definitions

    names = {d["function"]["name"] for d in scheduler_run_tool_definitions()}

    assert "start_task" in names
    assert "schedule_reminder" not in names
    assert "schedule_action" not in names
    assert "activate_skill" not in names
    # The chat catalogue is still there: a run looks things up with the same tools.
    assert "get_current_time" in names
