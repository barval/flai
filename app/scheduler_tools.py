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

from datetime import UTC, datetime, time, timedelta
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
# A year of minutes: past it the "in N minutes" field is a mistake, not a schedule.
_MAX_IN_MINUTES = 60 * 24 * 365
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


def parse_time_of_day(raw: str | None) -> time | None:
    """A bare ``HH:MM``, which is what a request naming only a time carries.

    The model has no reliable clock and invented a date once already (31 January for a
    request made in November). A time alone is the part it does know; the date belongs to
    the server, which has a clock.
    """
    text = " ".join(str(raw or "").split())
    if not text or " " in text:
        return None
    for fmt in ("%H:%M", "%H.%M"):
        try:
            return datetime.strptime(text, fmt).time()
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


def plan(
    kind: str,
    when_raw: str | None,
    repeat_raw: str | None,
    every_minutes,
    zone_name: str,
    now=None,
    in_minutes=None,
):
    """Resolve the phrase into a concrete moment, the recurrence fields and the local time.

    ``in_minutes`` is the relative form ("in two minutes") and takes precedence: a small
    model that has to convert "in two minutes" into an absolute date invents one — measured
    on the live instance, it wrote 2024-05-21 for a request made in 2026. The relative form
    has no date to get wrong.

    Returns ``(moment_utc, local_moment, fields)`` or raises :class:`ScheduleRefusalError`.
    """
    from app.scheduler import next_after

    current = now or datetime.now(UTC)
    zone = zone_for(zone_name)
    repeat = _repeat_of(repeat_raw)

    # The interval is resolved before the moment, because every path below stores it and the
    # floor applies to all of them: a five-minute action on a CPU-only host must be refused
    # whether the request named a date, a bare time or "in five minutes".
    interval = None
    if repeat == "minutes":
        from app.scheduler import min_interval_minutes

        floor = min_interval_minutes(kind)
        try:
            requested = int(every_minutes or floor)
        except (TypeError, ValueError) as exc:
            raise ScheduleRefusalError("repeat_unclear", value=str(every_minutes)) from exc
        if requested < floor:
            # Asking for ten minutes and quietly getting fifteen is worse than being told:
            # the person would believe a rate they never receive.
            raise ScheduleRefusalError("interval_too_small", floor=floor, kind=kind, asked=requested)
        interval = requested

    if in_minutes in (None, "") and not str(when_raw or "").strip():
        if repeat != "minutes":
            raise ScheduleRefusalError("time_unclear")
        # "Every five minutes" names no first moment: the first firing is one interval away.
        in_minutes = interval

    bare = parse_time_of_day(when_raw)
    if bare is not None:
        local_now = current.astimezone(zone)
        local = datetime.combine(local_now.date(), bare, tzinfo=zone)
        if local <= local_now:
            # "13:23" said at 13:30 means tomorrow, not a refusal.
            local = local + timedelta(days=1)
        return local.astimezone(UTC), local, _fields(local, repeat, interval, zone_name)

    if in_minutes not in (None, ""):
        try:
            minutes = int(in_minutes)
        except (TypeError, ValueError) as exc:
            raise ScheduleRefusalError("time_unclear") from exc
        if minutes < 1 or minutes > _MAX_IN_MINUTES:
            raise ScheduleRefusalError("time_unclear")
        moment = current + timedelta(minutes=minutes)
        local = moment.astimezone(zone)
        return moment, local, _fields(local, repeat, interval, zone_name)

    moment_local = parse_when(when_raw)
    if moment_local is None:
        raise ScheduleRefusalError("time_unclear")

    local = moment_local.replace(tzinfo=zone)
    moment = local.astimezone(UTC)

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
                "kind": kind,
            },
            current,
        )
        if moment is None or moment <= current:
            raise ScheduleRefusalError("in_the_past", when=local)
        local = moment.astimezone(zone)

    return moment, local, _fields(local, repeat, interval, zone_name)


def _fields(local: datetime, repeat: str, interval, zone_name: str) -> dict:
    """The recurrence columns for a firing at ``local``."""
    return {
        "repeat": repeat,
        "time_of_day": local.time().replace(second=0, microsecond=0),
        "weekday": local.weekday(),
        "day_of_month": local.day,
        "every_minutes": interval,
        "timezone": zone_name,
    }


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
    in_minutes=None,
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

    moment, local, fields = plan(
        kind, when_raw, repeat, every_minutes, zone_name, now=now, in_minutes=in_minutes
    )
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
