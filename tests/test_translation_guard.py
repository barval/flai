"""A truncated .mo must degrade the language, not break every page."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.translation_guard import MO_MAGIC, check_catalogs


def _catalog(root: Path, lang: str, payload: bytes) -> Path:
    path = root / lang / "LC_MESSAGES" / "messages.mo"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


@pytest.mark.unit
def test_an_empty_catalog_is_moved_aside(tmp_path: Path):
    # Measured live: an interrupted pybabel compile left this file at 0 bytes and
    # every English page answered 500 with struct.error.
    path = _catalog(tmp_path, "en", b"")
    moved = check_catalogs(tmp_path)
    assert moved == [str(path)]
    assert not path.exists()
    assert path.with_suffix(".mo.broken").exists()


@pytest.mark.unit
def test_a_truncated_but_non_empty_catalog_is_moved_aside(tmp_path: Path):
    path = _catalog(tmp_path, "ru", b"garbage")
    assert check_catalogs(tmp_path) == [str(path)]
    assert not path.exists()


@pytest.mark.unit
def test_a_valid_catalog_is_left_alone(tmp_path: Path):
    path = _catalog(tmp_path, "ru", MO_MAGIC + b"\x00" * 64)
    assert check_catalogs(tmp_path) == []
    assert path.read_bytes().startswith(MO_MAGIC)


@pytest.mark.unit
def test_a_missing_catalog_is_not_an_error(tmp_path: Path):
    # The language simply has no compiled catalog; Babel falls back to the ids.
    assert check_catalogs(tmp_path) == []


@pytest.mark.unit
def test_the_shipped_catalogs_are_valid():
    # The real files, so a broken commit cannot land quietly.
    root = Path(__file__).resolve().parent.parent / "translations"
    for lang in ("en", "ru"):
        catalog = root / lang / "LC_MESSAGES" / "messages.mo"
        assert catalog.is_file(), catalog
        assert catalog.stat().st_size > 1000, f"{catalog} looks truncated"
        assert catalog.read_bytes().startswith(MO_MAGIC), f"{catalog} is not a compiled catalog"
