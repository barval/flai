"""Real user data must never be committable, even by accident.

The RAG evaluation reports and datasets are built from the installation's own
documents: the questions are the user's, the expected answers are their facts, and
the matched fragments are their text. `data/` is ignored, so git never sees them —
but an ignore rule is only as good as the next `git add -f`, so this test states
the invariant and fails the build if anything under `data/` is ever tracked.

The release gate (`check_secrets.py`) looks at credential shapes; this one looks
at the *location*: a file that is user data by definition must not be in the tree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.mark.unit
def test_no_user_data_file_is_tracked():
    tracked = _git("ls-files", "data")
    assert tracked == "", f"user data must never be committed: {tracked.splitlines()[:5]}"


@pytest.mark.unit
def test_the_eval_reports_are_ignored():
    # The reports embed real questions, answers and matched fragments.
    sample = "data/rag_eval/grid-example.json"
    assert _git("check-ignore", sample) == sample


@pytest.mark.unit
def test_the_whole_data_tree_is_ignored():
    for path in ("data/documents/u/1.txt", "data/uploads/photo.png", "data/branding/logo.png", "data/backups/x.sql"):
        assert _git("check-ignore", path) == path, path


@pytest.mark.unit
def test_user_data_is_not_in_the_docker_build_context():
    # A bind mount or a published image would ship the same private files.
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "data/" in {line.strip() for line in ignored}
