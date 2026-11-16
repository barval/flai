# tests/graph_fakes.py
"""Shared stubs for the knowledge-graph tests.

The suite patches ``redis.from_url`` to a mock whose ``exists()`` is truthy;
every extraction would then look interrupted. These fakes give the graph code
real, inspectable state without a server.
"""


class FakeRedis:
    """Just enough Redis for the extractor: flags and two queues."""

    def __init__(self):
        self.values: dict = {}
        self.lists: dict = {}
        self.sorted_sets: dict = {}
        self.seq = 0

    def exists(self, key):
        return 1 if key in self.values else 0

    def set(self, key, value, ex=None):
        self.values[key] = value
        return True

    def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)
        return 1

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return 1

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return 1

    def llen(self, key):
        return len(self.lists.get(key, []))

    # The ready queue is a sorted set ranked by class (app/priority.py).
    def incr(self, key):
        self.seq += 1
        return self.seq

    def zadd(self, key, mapping):
        self.sorted_sets.setdefault(key, {}).update(mapping)
        return len(mapping)

    def zcard(self, key):
        return len(self.sorted_sets.get(key, {}))

    def zcount(self, key, low, high):
        return len(self.sorted_sets.get(key, {}))

    def zrange(self, key, start, end, withscores=False):
        items = sorted(self.sorted_sets.get(key, {}).items(), key=lambda pair: pair[1])
        return [member for member, _score in items]
