"""A class orders the ready queue, and no class waits longer than the quantum.

The three properties are what the FIFO list could not give: a class-0 request behind
a burst of class-2 requests was served last, and a low class under a continuous
stream of high-class arrivals had no bound at all.
"""

from __future__ import annotations

import pytest

from app.priority import QUANTUM, choose, class_of, normalize, score_for


class Lane:
    """The ready queue as Redis holds it: ordered by score, then by insertion."""

    def __init__(self) -> None:
        self.entries: list[tuple[int, str]] = []
        self.seq = 0
        self.skipped = 0

    def add(self, user_class: int) -> str:
        self.seq += 1
        member = f"{self.seq:06d}"
        self.entries.append((score_for(user_class, self.seq), member))
        self.entries.sort()
        return member

    def pop(self) -> tuple[str | None, int | None]:
        entry, self.skipped = choose(self.entries, self.skipped)
        if entry is None:
            return None, None
        self.entries.remove(entry)
        return entry[1], class_of(entry[0])


@pytest.mark.unit
def test_a_higher_class_does_not_queue_behind_a_burst():
    """Metric A: the class-0 request behind twenty class-2 requests is served first."""
    lane = Lane()
    for _ in range(20):
        lane.add(2)
    urgent = lane.add(0)

    served, user_class = lane.pop()
    assert user_class == 0
    assert served == urgent


@pytest.mark.unit
def test_the_lowest_class_never_waits_more_than_the_quantum():
    """Metric B: a continuous stream of class-0 arrivals cannot starve class 2.

    A class-0 request arrives for every pop, so arrivals always outpace a single
    worker — the case a deadline cannot survive, because the backlog that arrived
    before the deadline never drains. The class-2 request must still reach a slot.
    """
    lane = Lane()
    patient = lane.add(2)
    pops = 0
    served = None
    while pops < 4 * QUANTUM:
        lane.add(0)
        served, _ = lane.pop()
        pops += 1
        if served == patient:
            break
    assert served == patient, "the lowest class never reached a slot"
    assert pops <= QUANTUM


@pytest.mark.unit
def test_a_single_class_workload_stays_in_arrival_order():
    """Metric C: nothing changes for a queue where every request has one class."""
    lane = Lane()
    order = [lane.add(2) for _ in range(100)]
    served = []
    for _ in range(100):
        member, user_class = lane.pop()
        assert user_class == 2
        served.append(member)
    assert served == order


@pytest.mark.unit
def test_a_broken_class_is_the_default_and_never_the_top():
    assert normalize(None) == 2
    assert normalize("garbage") == 2
    assert normalize(-5) == 0
    assert normalize(99) == 5
    # The default must not outrank an explicitly assigned class.
    assert score_for("garbage", 1) > score_for(0, 1)


@pytest.mark.unit
def test_the_score_keeps_class_and_arrival_order_apart():
    assert class_of(score_for(0, 1)) == 0
    assert class_of(score_for(5, 1)) == 5
    # A later request of a better class still wins.
    assert score_for(0, 10_000) < score_for(1, 1)
    # Inside one class the earlier request is served first, with no clock involved.
    assert score_for(2, 1) < score_for(2, 2)


@pytest.mark.unit
def test_the_skip_counter_resets_when_a_class_is_served():
    lane = Lane()
    lane.add(2)
    for _ in range(QUANTUM - 1):
        lane.add(0)
        lane.pop()
    assert lane.skipped == QUANTUM - 1
    _, user_class = lane.pop()
    assert user_class == 2, "the quantum must reach the patient class"
    assert lane.skipped == 0


class FakeReadySet:
    """The sorted-set calls ``RedisRequestQueue._pop_ready`` makes, in memory."""

    def __init__(self) -> None:
        self.data: dict[str, float] = {}
        self.seq = 0
        self.deleted: list[str] = []
        self.list_payloads: dict[str, list[str]] = {}
        self.kind = "none"

    # sorted set
    def zadd(self, key, mapping):
        self.data.update(mapping)
        return 1

    def zrange(self, key, start, end, withscores=False):
        items = sorted((score, member) for member, score in self.data.items())
        if end == -1:
            end = len(items) - 1
        chunk = items[start : end + 1]
        return [(member, score) for score, member in chunk] if withscores else [member for _, member in chunk]

    def zrangebyscore(self, key, low, high, start=0, num=1):
        items = sorted((score, member) for member, score in self.data.items() if low <= score <= high)
        return [member for _, member in items[start : start + num]]

    def zrem(self, key, member):
        return 1 if self.data.pop(member, None) is not None else 0

    def bzpopmin(self, key, timeout=None):
        if not self.data:
            return None
        member, score = min(self.data.items(), key=lambda kv: kv[1])
        del self.data[member]
        return (key, member, score)

    def zcard(self, key):
        return len(self.data)

    def zcount(self, key, low, high):
        return sum(1 for score in self.data.values() if low == "-inf" or score >= low)

    def incr(self, key):
        self.seq += 1
        return self.seq

    # list side, for the migration
    def type(self, key):
        return "list" if self.list_payloads.get(key) else self.kind

    def lrange(self, key, start, end):
        return self.list_payloads.get(key, [])

    def hlen(self, key):
        return 0

    def delete(self, key):
        self.deleted.append(key)
        self.list_payloads.pop(key, None)
        return 1


def _queue_with(fake):
    from unittest.mock import Mock, patch

    from app.queue import RedisRequestQueue

    app = Mock()
    app.config = {"REDIS_URL": "redis://localhost:6379/0", "SECRET_KEY": "test"}
    app.logger = Mock()
    with (
        patch("app.queue.redis.from_url", return_value=fake),
        patch("app.queue.RedisRequestQueue.start_worker"),
    ):
        return RedisRequestQueue(app)


@pytest.mark.unit
def test_the_redis_path_serves_a_better_class_first():
    """The mapping, not just the policy: ZADD by score, then BZPOPMIN."""
    fake = FakeReadySet()
    queue = _queue_with(fake)
    for _ in range(20):
        queue._enqueue_ready(queue.queue_key, "burst", user_class=2)
    queue._enqueue_ready(queue.queue_key, "urgent", user_class=0)

    assert queue._pop_ready(queue.queue_key, timeout=1) == "urgent"
    assert fake.data.get("urgent") is None


@pytest.mark.unit
def test_the_redis_path_reaches_the_lowest_class_within_the_quantum():
    """The override is what makes the bound real: every pop is taken by class 0."""
    fake = FakeReadySet()
    queue = _queue_with(fake)
    queue._enqueue_ready(queue.queue_key, "patient", user_class=2)

    served = None
    pops = 0
    while pops < 4 * QUANTUM:
        queue._enqueue_ready(queue.queue_key, "urgent", user_class=0)
        served = queue._pop_ready(queue.queue_key, timeout=1)
        pops += 1
        if served == "patient":
            break
    assert served == "patient"
    assert pops <= QUANTUM


@pytest.mark.unit
def test_startup_moves_tasks_out_of_the_old_fifo_lists():
    """An upgrade must not lose the requests that were already waiting."""
    fake = FakeReadySet()
    queue = _queue_with(fake)
    fake.list_payloads[queue.queue_key] = ["first", "second"]

    moved = queue._migrate_ready_queues()

    assert moved == 2
    assert set(fake.data) == {"first", "second"}
    assert queue.queue_key in fake.deleted
