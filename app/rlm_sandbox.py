# app/rlm_sandbox.py
"""Isolated Python executor for RLM analysis.

Model-generated code runs in a persistent forked child process with an AST
whitelist and rlimits. The child has no network or filesystem access;
llm()/web_fetch() requests travel back to the parent over IPC.
"""

from __future__ import annotations

import ast
import contextlib
import io
import math
import multiprocessing
import os
import re
import resource
import time
from dataclasses import dataclass
from typing import Any, Protocol

FORBIDDEN_NODES: tuple[type[ast.AST], ...] = (
    ast.Import,
    ast.ImportFrom,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.With,
    ast.AsyncWith,
    ast.Delete,
    ast.Global,
    ast.Nonlocal,
    ast.Yield,
    ast.YieldFrom,
    ast.Await,
    ast.AsyncFor,
    ast.Lambda,
)

FORBIDDEN_NAMES: set[str] = {
    "eval",
    "exec",
    "compile",
    "open",
    "input",
    "__import__",
    "globals",
    "locals",
    "vars",
    "getattr",
    "setattr",
    "delattr",
    "exit",
    "quit",
    "breakpoint",
    "memoryview",
}

SAFE_BUILTINS: dict[str, Any] = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "chr": chr,
    "dict": dict,
    "divmod": divmod,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "int": int,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "iter": iter,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "next": next,
    "ord": ord,
    "pow": pow,
    "print": print,
    "range": range,
    "repr": repr,
    "reversed": reversed,
    "round": round,
    "set": set,
    "slice": slice,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
    "True": True,
    "False": False,
    "None": None,
}


def validate_code(code: str) -> str | None:
    """Return None if the code is permitted, else an error description."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        return f"SyntaxError: {e.msg}"
    except RecursionError:
        return "Code too deeply nested"
    for node in ast.walk(tree):
        if isinstance(node, FORBIDDEN_NODES):
            return f"Forbidden syntax: {type(node).__name__}"
        if isinstance(node, ast.Name) and (node.id in FORBIDDEN_NAMES or node.id.startswith("__")):
            return f"Forbidden name: {node.id}"
        if isinstance(node, ast.Attribute) and (node.attr.startswith("__") or node.attr == "format"):
            return f"Forbidden attribute: {node.attr}"
    return None


class _FinalSignal(Exception):  # noqa: N818 - control-flow signal, not an error
    def __init__(self, answer: str) -> None:
        super().__init__("final")
        self.answer = answer


def _raise_final(answer: str) -> None:
    raise _FinalSignal(str(answer))


@dataclass
class SandboxResult:
    ok: bool
    output: str = ""
    error: str = ""
    final: str | None = None


class SandboxBroker(Protocol):
    def llm(self, prompt: str, text: str = "") -> str: ...

    def web_fetch(self, query: str) -> str: ...


def _compile_body(code: str):
    tree = ast.parse(code, mode="exec")
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = tree.body[-1]
        tree.body[-1] = ast.Assign(
            targets=[ast.Name(id="_rlm_result", ctx=ast.Store())],
            value=last.value,
        )
    ast.fix_missing_locations(tree)
    return compile(tree, "<rlm>", "exec")


def _callback(conn, name: str, args: dict[str, Any]) -> Any:
    conn.send(("callback", name, args))
    reply = conn.recv()
    if reply[0] != "cb_result":
        raise RuntimeError("sandbox protocol error")
    payload = reply[1]
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error", "callback failed"))
    return payload["value"]


def _apply_rlimits(memory_mb: int, cpu_seconds: int) -> None:
    limit = memory_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _child_loop(conn, corpus: dict[str, str], memory_mb: int, cpu_seconds: int, max_output: int) -> None:
    _apply_rlimits(memory_mb, cpu_seconds)
    namespace: dict[str, Any] = {
        "context": corpus,
        "re": re,
        "math": math,
        "__builtins__": SAFE_BUILTINS,
        "llm": lambda prompt, text="": _callback(conn, "llm", {"prompt": prompt, "text": text}),
        "web_fetch": lambda query: _callback(conn, "web_fetch", {"query": query}),
        "final": _raise_final,
    }
    while True:
        msg = conn.recv()
        if msg[0] == "shutdown":
            return
        _, code = msg
        buf = io.StringIO()
        try:
            compiled = _compile_body(code)
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                exec(compiled, namespace, namespace)
            value = namespace.pop("_rlm_result", None)
            text = (value if isinstance(value, str) else repr(value)) if value is not None else buf.getvalue()
            conn.send(("done", text[:max_output], None))
        except _FinalSignal as f:
            conn.send(("done", "", f.answer))
        except BaseException as e:  # noqa: BLE001 - sandbox must report everything
            conn.send(("error", f"{type(e).__name__}: {e}"))


class RlmSandbox:
    def __init__(
        self,
        corpus: dict[str, str],
        broker: SandboxBroker,
        *,
        code_timeout: float = 15.0,
        max_output: int = 8000,
        memory_mb: int = 2048,
        cpu_seconds: int = 120,
    ) -> None:
        self.corpus = corpus
        self.broker = broker
        self.code_timeout = code_timeout
        self.max_output = max_output
        self.memory_mb = memory_mb
        self.cpu_seconds = cpu_seconds
        self._conn: Any = None
        self._pid: int | None = None

    def start(self) -> None:
        parent_conn, child_conn = multiprocessing.Pipe(duplex=True)
        pid = os.fork()
        if pid == 0:
            parent_conn.close()
            try:
                _child_loop(child_conn, self.corpus, self.memory_mb, self.cpu_seconds, self.max_output)
            finally:
                os._exit(0)
        child_conn.close()
        self._conn = parent_conn
        self._pid = pid

    def _handle_callback(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "llm":
                return {"ok": True, "value": self.broker.llm(args.get("prompt", ""), args.get("text", ""))}
            if name == "web_fetch":
                return {"ok": True, "value": self.broker.web_fetch(args.get("query", ""))}
            return {"ok": False, "error": f"Unknown callback: {name}"}
        except Exception as e:  # noqa: BLE001 - broker errors are data for the model
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def exec(self, code: str) -> SandboxResult:
        error = validate_code(code)
        if error:
            return SandboxResult(ok=False, error=error)
        if self._conn is None:
            return SandboxResult(ok=False, error="sandbox not started")
        try:
            return self._exec_io(code)
        except (EOFError, BrokenPipeError, OSError) as e:
            self._kill()
            return SandboxResult(ok=False, error=f"sandbox died: {type(e).__name__}: {e}")

    def _exec_io(self, code: str) -> SandboxResult:
        self._conn.send(("exec", code))
        deadline = time.time() + self.code_timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0 or not self._conn.poll(remaining):
                self._kill()
                return SandboxResult(ok=False, error="execution timeout")
            msg = self._conn.recv()
            kind = msg[0]
            if kind == "callback":
                self._conn.send(("cb_result", self._handle_callback(msg[1], msg[2])))
                deadline = time.time() + self.code_timeout
            elif kind == "done":
                return SandboxResult(ok=True, output=msg[1], final=msg[2])
            elif kind == "error":
                return SandboxResult(ok=False, error=msg[1])

    def _kill(self) -> None:
        if self._pid:
            with contextlib.suppress(ProcessLookupError):
                os.kill(self._pid, 9)
            with contextlib.suppress(ChildProcessError):
                os.waitpid(self._pid, 0)
            self._pid = None
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
            self._conn = None

    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.send(("shutdown",))
        if self._pid:
            deadline = time.time() + 2
            while time.time() < deadline:
                try:
                    done, _ = os.waitpid(self._pid, os.WNOHANG)
                except ChildProcessError:
                    done = self._pid
                if done:
                    break
                time.sleep(0.05)
            else:
                self._kill()
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
            self._conn = None
        self._pid = None
