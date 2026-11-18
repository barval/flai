"""Scheduled items created from chat: the model names the time, this module checks it.

The model is good at understanding "tomorrow at nine" and bad at knowing today's date in
a zone it cannot see, so the tool takes a wall-clock moment ("2026-11-18 09:00") in the
installation's zone and decides the rest here. A moment in the past is refused with a
question for a one-off; a repeating phrase that named a time already gone today moves to
its next occurrence, because "every day at nine" said at ten means tomorrow at nine.

The answer always names the stored moment: a wrong date is the one mistake the user
cannot see until the wrong moment arrives.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

# Parsing a moment is deterministic and cheap; picking it apart from prose is the model's
# job, which is why nothing here reads the message text.
_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%d.%m.%Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%d.%m.%Y",
    "%Y-%m-%d",
)

REPEATS = ("once", "minutes", "daily", "weekly", "monthly")
MIN_EVERY_MINUTES = 15
MAX_BODY_CHARS = 4000
MAX_TITLE_CHARS = 120


class ScheduleRefusalError(Exception):
    """A creation refused with a reason the caller turns into a question."""

    def __init__(self, code: str, **params):
        super().__init__(code)
        self.code = code
        self.params = params


def zone_for(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or "UTC"))
    except Exception:  # noqa: BLE001 - an unknown zone must not stop a reminder
        return ZoneInfo("UTC")


def installation_zone_name(app) -> str:
    name = str(app.config.get("TIMEZONE_STR") or "").strip()
    if not name:
        return "UTC"
    try:
        ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return "UTC"
    return name


def parse_when(raw: str | None) -> datetime | None:
    """A naive wall-clock moment, or None when the text is not one."""
    text = " ".join(str(raw or "").split())
    if not text:
        return None
    for fmt in _FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _repeat_of(raw: str | None) -> str:
    repeat = str(raw or "once").strip().lower()
    if repeat not in REPEATS:
        raise ScheduleRefusalError("repeat_unclear", value=repeat)
    if repeat == "minutes":
        pass
    return repeat


def plan(kind: str, when_raw: str | None, repeat_raw: str | None, every_minutes, zone_name: str, now=None):
    """Resolve the phrase into a concrete moment, the recurrence fields and the local time.

    Returns ``(moment_utc, local_moment, fields)`` or raises :class:`ScheduleRefusalError`.
    """
    from app.scheduler import next_after

    moment_local = parse_when(when_raw)
    if moment_local is None:
        raise ScheduleRefusalError("time_unclear")

    repeat = _repeat_of(repeat_raw)
    zone = zone_for(zone_name)
    local = moment_local.replace(tzinfo=zone)
    moment = local.astimezone(UTC)
    current = now or datetime.now(UTC)

    interval = None
    if repeat == "minutes":
        try:
            interval = max(MIN_EVERY_MINUTES, int(every_minutes or MIN_EVERY_MINUTES))
        except (TypeError, ValueError) as exc:
            raise ScheduleRefusalError("repeat_unclear", value=str(every_minutes)) from exc

    if moment <= current:
        if repeat == "once":
            raise ScheduleRefusalError("in_the_past", when=local)
        # A repeating phrase can name a time that already passed today; its next
        # occurrence is what the person meant.
        moment = next_after(
            {
                "repeat": repeat,
                "every_minutes": interval,
                "weekday": local.weekday(),
                "day_of_month": local.day,
                "time_of_day": local.time(),
                "timezone": zone_name,
            },
            current,
        )
        if moment is None or moment <= current:
            raise ScheduleRefusalError("in_the_past", when=local)
        local = moment.astimezone(zone)

    fields = {
        "repeat": repeat,
        "time_of_day": local.time().replace(second=0, microsecond=0),
        "weekday": local.weekday(),
        "day_of_month": local.day,
        "every_minutes": interval,
        "timezone": zone_name,
    }
    return moment, local, fields


def create_item(
    kind: str,
    user_id: str,
    when_raw: str | None,
    body: str,
    title: str = "",
    repeat: str | None = None,
    every_minutes=None,
    zone_name: str = "UTC",
    now=None,
):
    """Store one scheduled item; return ``(item_id, local_moment)``."""
    from app import scheduler_store

    text = str(body or "").strip()
    if kind not in ("reminder", "action"):
        raise ScheduleRefusalError("kind_unclear")
    if not text:
        raise ScheduleRefusalError("empty_body")
    if len(text) > MAX_BODY_CHARS:
        raise ScheduleRefusalError("too_long", limit=MAX_BODY_CHARS)

    moment, local, fields = plan(kind, when_raw, repeat, every_minutes, zone_name, now=now)
    item_id = scheduler_store.create_item(
        user_id,
        kind=kind,
        title=str(title or "").strip()[:MAX_TITLE_CHARS] or text[:60],
        body=text,
        repeat=fields["repeat"],
        at=moment,
        time_of_day=fields["time_of_day"],
        weekday=fields["weekday"],
        day_of_month=fields["day_of_month"],
        every_minutes=fields["every_minutes"],
        timezone_name=fields["timezone"],
    )
    return item_id, local
