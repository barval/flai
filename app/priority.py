"""Queue priority: a class orders the queue, and a quantum bounds the wait.

Every request carries a class — 0/1/2 from ``users.service_class``, 3/4/5 for the
background tasks derived from it — but the queue is a plain Redis list, so a class
has only ever existed in the payload: a class-0 request waits behind every class-2
request that arrived first.

The ready queue is a sorted set whose score is ``class * STRIDE + seq``:

* **A class orders the queue.** A class-0 request sorts ahead of every class-2
  request already waiting, and inside one class the sequence term keeps arrival
  order exactly, so a single-class workload behaves as the list did.
* **A class waits at most ``QUANTUM`` pops.** A deadline cannot bound this: a
  low-class request whose deadline has passed still sits behind the backlog that
  arrived before that deadline, and when arrivals outpace service the backlog never
  drains. So the worker counts consecutive pops that skipped the lowest waiting
  class and, at ``QUANTUM``, takes that class next. The bound is in pops, which is
  what a queue can actually promise.

The sequence is a Redis counter, not a clock: ``int(time * 1000)`` ties every request
of one burst, and a wall-clock score buys nothing the sequence does not give.
"""

from __future__ import annotations

from typing import Any

#: One class spans this many units, so the class term dominates the sequence term.
#: 5 * 10**12 + 10**12 stays far inside the exactly representable integer range of a
#: double (2**53 ≈ 9.0 * 10**15), which matters because Redis sorts scores as doubles.
STRIDE = 10**12

#: Classes 0/1/2 come from the user profile, 3/4/5 from the task it derives.
MIN_CLASS = 0
MAX_CLASS = 5
DEFAULT_CLASS = 2

#: Pops after which the lowest waiting class is served whether or not it is the
#: highest-priority one. Eight pops bound a class-2 request to eight service slots.
QUANTUM = 8


def normalize(user_class: Any) -> int:
    """The class as an int inside the known range; anything unusable is the default.

    A queue must not fail because a payload carried a string, and the default is the
    class every account starts with rather than the highest one: an unreadable class
    must not become a way to jump the queue.
    """
    try:
        value = int(user_class)
    except (TypeError, ValueError):
        return DEFAULT_CLASS
    return min(MAX_CLASS, max(MIN_CLASS, value))


def score_for(user_class: Any, seq: int) -> int:
    """The ready-queue score: class first, arrival order second."""
    return normalize(user_class) * STRIDE + max(0, int(seq))


def class_of(score: int | float | str) -> int:
    """The class encoded in a score."""
    return int(float(score)) // STRIDE


def band(user_class: Any) -> tuple[int, int]:
    """Score bounds ``[low, high]`` of one class, for ``ZRANGEBYSCORE``."""
    cls = normalize(user_class)
    return cls * STRIDE, (cls + 1) * STRIDE - 1


def choose(entries: list[tuple[int, str]], skipped: int) -> tuple[tuple[int, str] | None, int]:
    """Which entry to pop next, and the new count of pops that skipped a class.

    ``entries`` is the sorted view of the ready queue, ``(score, member)`` in
    ascending score order; ``skipped`` is how many consecutive pops went to a
    higher-priority class while a lower one was waiting. The worker mirrors this
    against Redis — ``ZRANGE 0 0`` for the head, ``ZRANGE -1 -1`` for the lowest
    waiting class — so the policy is testable without a server.
    """
    if not entries:
        return None, skipped
    head = entries[0]
    lowest = class_of(entries[-1][0])
    if lowest > class_of(head[0]):
        if skipped + 1 >= QUANTUM:
            for entry in entries:
                if class_of(entry[0]) == lowest:
                    return entry, 0
        return head, skipped + 1
    return head, 0
