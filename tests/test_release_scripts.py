"""The release gate must pass on the repository it ships with.

These are the scripts a release runs by hand. A script that reports a finding on a
clean tree teaches the operator to ignore it, and a script nobody runs is worse
than none — so each one is invoked here, on this checkout, and must exit `0`.

The database-backed checks are deliberately absent: this file stays a fast unit
run that needs no PostgreSQL and no Redis.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".opencode" / "skills" / "preparing-flai-release" / "scripts"

CLEAN_GATES = (
    "check_translations.py",
    "check_catalog_drift.py",
    "check_api_docs.py",
    "check_doc_headings.py",
    "check_readme_toc.py",
    "check_secrets.py",
    "check_dead_paths.py",
    "check_doc_claims.py",
    "check_skill_claims.py",
    "check_compose_profiles.py",
    "check_env_parity.py",
    "check_release_strings.py",
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=180
    )


@pytest.mark.unit
@pytest.mark.parametrize("script", CLEAN_GATES)
def test_a_release_gate_is_clean_on_this_tree(script: str) -> None:
    result = _run(str(SCRIPTS / script))
    assert result.returncode == 0, f"{script} reported findings:\n{result.stdout}\n{result.stderr}"


@pytest.mark.unit
def test_every_script_is_named_in_the_skill() -> None:
    """A helper the procedure never names is a helper nobody runs."""
    skill = (SCRIPTS.parent / "SKILL.md").read_text(encoding="utf-8")
    missing = [path.name for path in sorted(SCRIPTS.glob("*.py")) if path.name not in skill]
    assert missing == []


@pytest.mark.unit
def test_skill_claims_reports_a_dead_command(tmp_path: Path) -> None:
    """The checker has teeth: a command the code does not register is a finding."""
    script = SCRIPTS / "check_skill_claims.py"
    if not script.exists():
        pytest.skip("checker absent")

    target = tmp_path / ".opencode" / "skills" / "probe"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        "# probe\n\nRun `flask definitely-not-a-command` for the release.\n", encoding="utf-8"
    )
    (tmp_path / ".env.example").write_text("", encoding="utf-8")
    (tmp_path / "tests").mkdir()

    # The checker resolves paths from its own location, so point it at the probe by
    # copying the checker into a repository-shaped scratch tree.
    layout = tmp_path / ".opencode" / "skills" / "preparing-flai-release" / "scripts"
    layout.mkdir(parents=True)
    (layout / "check_skill_claims.py").write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "cli.py").write_text("", encoding="utf-8")
    (tmp_path / "app" / "config.py").write_text("", encoding="utf-8")
    (tmp_path / "app" / "settings_registry.py").write_text("", encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    probe = _run(str(layout / "check_skill_claims.py"))
    assert probe.returncode == 1
    assert "definitely-not-a-command" in probe.stdout
