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
