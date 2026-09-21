# tests/test_rlm_sandbox.py
import fcntl
import os

import pytest

from app.rlm_sandbox import validate_code


@pytest.mark.unit
def test_validate_code_allows_safe_indexing():
    code = "idx = [i for i, c in enumerate(context['doc']) if 'x' in c]\nlen(idx)"
    assert validate_code(code) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "code",
    [
        "import os",
        "from os import getenv",
        "def f():\n    return 1",
        "class C:\n    pass",
        "with open('/etc/passwd') as f:\n    pass",
        "del context",
        "global x",
        "eval('1+1')",
        "exec('x=1')",
        "open('/etc/passwd')",
        "__import__('os')",
        "context.__class__",
        "(1).__class__.__mro__",
        "x = lambda: 1",
    ],
)
def test_validate_code_rejects_forbidden_constructs(code):
    assert validate_code(code) is not None


@pytest.mark.unit
def test_validate_code_reports_syntax_error():
    err = validate_code("def (")
    assert err is not None and "Syntax" in err


@pytest.mark.unit
def test_validate_code_import_error_is_actionable():
    """The import refusal must tell the model WHY and WHAT to use instead —
    a bare 'Forbidden syntax: Import' gets repeated (2 steps burned in prod)."""
    err = validate_code("import json")
    assert err is not None
    assert "re" in err and "math" in err
    err2 = validate_code("from collections import defaultdict")
    assert err2 == err


@pytest.mark.unit
def test_validate_code_rejects_format_attribute():
    assert validate_code("'{0.__class__}'.format(context)") is not None


@pytest.mark.unit
def test_validate_code_handles_deep_nesting():
    err = validate_code("[" * 5000)
    assert err is not None


from app.rlm_sandbox import RlmSandbox  # noqa: E402


class FakeBroker:
    def __init__(self):
        self.llm_calls = []

    def llm(self, prompt, text=""):
        self.llm_calls.append((prompt, text))
        return f"sub:{prompt}:{len(text)}"

    def web_fetch(self, query):
        return f"web:{query}"


@pytest.mark.unit
def test_sandbox_persists_state_between_execs():
    sb = RlmSandbox({"doc": "abcdef"}, FakeBroker())
    sb.start()
    try:
        assert sb.exec("idx = [i for i, c in enumerate(context['doc']) if c in 'ce']").ok
        res = sb.exec("idx")
        assert res.ok and res.output == "[2, 4]"
    finally:
        sb.close()


@pytest.mark.unit
def test_sandbox_calls_broker_llm():
    broker = FakeBroker()
    sb = RlmSandbox({"doc": "hello world"}, broker)
    sb.start()
    try:
        res = sb.exec("llm('summarize', context['doc'])")
        assert res.ok and res.output == "sub:summarize:11"
        assert broker.llm_calls == [("summarize", "hello world")]
    finally:
        sb.close()


@pytest.mark.unit
def test_sandbox_final_terminates():
    sb = RlmSandbox({"doc": "x"}, FakeBroker())
    sb.start()
    try:
        res = sb.exec("final('done')")
        assert res.ok and res.final == "done"
    finally:
        sb.close()


@pytest.mark.unit
def test_sandbox_has_math():
    from app.rlm_sandbox import RlmSandbox

    sandbox = RlmSandbox({"doc": "test data"}, None)
    sandbox.start()
    try:
        result = sandbox.exec("final(str(math.sqrt(144)))")
        assert result.final == "12.0"
    finally:
        sandbox.close()


@pytest.mark.unit
def test_sandbox_blocks_import_at_runtime():
    sb = RlmSandbox({}, FakeBroker())
    sb.start()
    try:
        res = sb.exec("import os")
        assert not res.ok
        assert "Imports are forbidden" in res.error and "re" in res.error
    finally:
        sb.close()


@pytest.mark.unit
def test_sandbox_timeout_kills_runaway_code():
    sb = RlmSandbox({}, FakeBroker(), code_timeout=1.0)
    sb.start()
    try:
        res = sb.exec("while True:\n    pass")
        assert not res.ok and "timeout" in res.error.lower()
    finally:
        sb.close()


@pytest.mark.unit
def test_sandbox_truncates_huge_output():
    sb = RlmSandbox({}, FakeBroker(), max_output=50)
    sb.start()
    try:
        res = sb.exec("print('a' * 5000)")
        assert res.ok and len(res.output) == 50
    finally:
        sb.close()


@pytest.mark.unit
def test_sandbox_blocks_filesystem():
    sb = RlmSandbox({}, FakeBroker())
    sb.start()
    try:
        res = sb.exec("open('/etc/passwd').read()")
        assert not res.ok
    finally:
        sb.close()


@pytest.mark.unit
def test_sandbox_child_crash_returns_error_result():
    broker = FakeBroker()
    sb = RlmSandbox({"doc": "x"}, broker)
    sb.start()
    try:
        # Simulate the child dying right after start: kill it, then exec.
        os.kill(sb._pid, 9)
        os.waitpid(sb._pid, 0)
        sb._pid = None
        res = sb.exec("1+1")
        assert res.ok is False
        assert "sandbox died" in res.error
    finally:
        sb.close()


@pytest.mark.unit
def test_sandbox_uses_unidirectional_pipes_not_socketpair():
    # Regression: multiprocessing duplex pipes are socketpairs, which gevent
    # creates in non-blocking mode — the forked child died on its first recv
    # and every exec reported "sandbox died: BrokenPipe". The sandbox must
    # use unidirectional os.pipe pairs, which keep blocking behavior.
    broker = FakeBroker()
    sb = RlmSandbox({"doc": "x"}, broker)
    sb.start()
    try:
        for conn in (sb._conn, sb._reply_conn):
            flags = fcntl.fcntl(conn.fileno(), fcntl.F_GETFL)
            assert not (flags & os.O_NONBLOCK), "sandbox pipe is non-blocking (socketpair under gevent)"
    finally:
        sb.close()
