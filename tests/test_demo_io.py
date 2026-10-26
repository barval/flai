"""Tests for the deterministic snapshot io of the offline demo."""

from demo.io import ROOT, load_snapshot, read_app_version, save_snapshot


def test_save_is_deterministic(tmp_path):
    first = {"b": [1, {"z": 1, "a": 2}], "a": "Grüße"}
    second = {"a": "Grüße", "b": [1, {"a": 2, "z": 1}]}
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    save_snapshot(a, first)
    save_snapshot(b, second)
    assert a.read_bytes() == b.read_bytes()
    assert a.read_text("utf-8").endswith("\n")


def test_round_trip(tmp_path):
    path = tmp_path / "s.json"
    save_snapshot(path, {"lang": "ru", "routes": []})
    assert load_snapshot(path) == {"lang": "ru", "routes": []}


def test_read_app_version():
    assert read_app_version() == read_app_version(ROOT)
    assert read_app_version().count(".") == 1
