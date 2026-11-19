"""The deploy scripts are code that ships, and a missing argument stops an install.

`disable_env_key NET_TOOLS_ENABLED` was called without its message, and the function read
`$2` under `set -u`: the deployment died at that line on a machine that had never disabled
netdiag before, with a message about a variable boundary. The scripts are not executed by
the suite — a syntax check and a call-shape check are what can run here.
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ("deploy.sh", "deploy-ru.sh")


@pytest.mark.unit
@pytest.mark.parametrize("name", SCRIPTS)
def test_the_script_parses(name: str) -> None:
    result = subprocess.run(["bash", "-n", str(ROOT / name)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.unit
@pytest.mark.parametrize("name", SCRIPTS)
def test_every_env_key_call_carries_its_message(name: str) -> None:
    """A call with only a key reaches `$2` unbound and aborts the install."""
    text = (ROOT / name).read_text(encoding="utf-8")
    bare = [
        line.strip()
        for line in text.splitlines()
        if re.match(r"^\s*(enable|disable)_env_key\s+[A-Z0-9_]+\s*$", line)
    ]
    assert bare == [], f"{name}: env-key call without a message: {bare}"


@pytest.mark.unit
@pytest.mark.parametrize("name", SCRIPTS)
def test_the_env_key_helpers_do_not_require_a_second_argument(name: str) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    assert 'local key="$1" message="$2"' not in text, f"{name}: `$2` aborts under set -u"
