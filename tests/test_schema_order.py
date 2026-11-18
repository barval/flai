"""`init_db()` runs on an empty database, so every ALTER needs its CREATE first.

The live installation already has the schema, which is why a misplaced statement
survives there: `ALTER TABLE task_steps` ran a hundred lines above the
`CREATE TABLE IF NOT EXISTS task_steps` that makes the table, and the first start on a
clean database died with `UndefinedTable`. CI found it; this test keeps it found.
"""

import re
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "app" / "database.py"


def _function_body(name: str) -> str:
    text = SOURCE.read_text(encoding="utf-8")
    start = text.index(f"def {name}(")
    end = text.find("\ndef ", start + 1)
    return text[start : end if end != -1 else len(text)]


@pytest.mark.unit
def test_schema_statements_run_in_an_order_a_fresh_database_accepts():
    body = _function_body("_init_postgresql")
    creates = {
        match.group(1).lower(): match.start()
        for match in re.finditer(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", body)
    }

    misplaced = [
        name
        for name, position in (
            (match.group(1).lower(), match.start()) for match in re.finditer(r"ALTER TABLE\s+(\w+)", body)
        )
        if name in creates and position < creates[name]
    ]

    assert misplaced == [], f"ALTER before CREATE in _init_postgresql: {misplaced}"


@pytest.mark.unit
def test_the_steps_table_is_created_where_its_alter_runs():
    body = _function_body("_init_postgresql")

    assert re.search(r"CREATE TABLE IF NOT EXISTS\s+task_steps", body)
    assert re.search(r"ALTER TABLE\s+task_steps\s+ADD COLUMN IF NOT EXISTS extra", body)
