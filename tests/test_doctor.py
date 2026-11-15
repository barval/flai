"""`flask doctor`: the readiness report and its failure modes."""

from __future__ import annotations

from types import SimpleNamespace

from app import doctor


def _check(name: str, status: str = doctor.OK) -> doctor.Check:
    return doctor.Check(name, status, name.title(), "detail")


def test_a_report_is_ready_when_every_check_passes(monkeypatch):
    monkeypatch.setattr(doctor, "CHECKS", (lambda app: _check("database"), lambda app: _check("redis")))

    report = doctor.run_checks(SimpleNamespace())

    assert report.as_dict()["status"] == doctor.OK
    assert not report.failures
    assert "ready" in doctor.render(report)


def test_a_raising_probe_becomes_a_failed_check(monkeypatch):
    def _explode(_app):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(doctor, "CHECKS", (lambda app: _check("database"), _explode))

    report = doctor.run_checks(SimpleNamespace())

    assert [check.id for check in report.failures] == ["_explode"]
    assert "connection refused" in report.failures[0].detail


def test_a_failure_hides_the_ready_verdict_and_shows_its_hint(monkeypatch):
    failing = doctor.Check("models", doctor.FAIL, "Models", "qwen.gguf is missing", "download it")
    monkeypatch.setattr(doctor, "CHECKS", (lambda app: _check("database"), lambda app: failing))

    rendered = doctor.render(doctor.run_checks(SimpleNamespace()))

    assert "ready" not in rendered
    assert "qwen.gguf is missing" in rendered
    assert "download it" in rendered


def test_a_missing_projector_fails_the_models_check(monkeypatch):
    monkeypatch.setattr("app.universal_mode.is_enabled", lambda: False)
    monkeypatch.setattr(
        "app.model_config.get_model_config",
        lambda module, _raw=False: {"model_name": "qwen.gguf"} if module == "multimodal" else {},
    )
    monkeypatch.setattr("app.universal_mode.model_path_for", lambda name: f"/models/{name}")
    monkeypatch.setattr("app.universal_mode.mmproj_for", lambda name: None)
    monkeypatch.setattr(doctor.os.path, "exists", lambda path: True)

    check = doctor._check_models(SimpleNamespace())

    assert check.status == doctor.FAIL
    assert "vision projector" in check.detail


def test_no_configured_model_fails_the_models_check(monkeypatch):
    monkeypatch.setattr("app.model_config.get_model_config", lambda module, _raw=False: {})

    check = doctor._check_models(SimpleNamespace())

    assert check.status == doctor.FAIL
    assert "no model is configured" in check.detail


def test_storage_names_the_folder_that_is_missing(tmp_path):
    app = SimpleNamespace(
        config={"DOCUMENTS_FOLDER": str(tmp_path / "documents"), "UPLOAD_FOLDER": str(tmp_path)}
    )

    check = doctor._check_storage(app)

    assert check.status == doctor.FAIL
    assert "DOCUMENTS_FOLDER" in check.detail
    assert "UPLOAD_FOLDER" not in check.detail


def test_writable_folders_pass_the_storage_check(tmp_path):
    app = SimpleNamespace(config={"DOCUMENTS_FOLDER": str(tmp_path), "UPLOAD_FOLDER": str(tmp_path)})

    assert doctor._check_storage(app).status == doctor.OK


def test_single_model_mode_ignores_the_pair_rows(monkeypatch):
    """The universal row serves both roles; the pair's files may be absent."""
    monkeypatch.setattr("app.universal_mode.is_enabled", lambda: True)
    monkeypatch.setattr("app.universal_mode.model_path_for", lambda name: f"/models/{name}")
    monkeypatch.setattr("app.universal_mode.mmproj_for", lambda name: "/models/mmproj-x.gguf")
    monkeypatch.setattr(
        "app.model_config.get_model_config",
        lambda module, _raw=False: {"model_name": f"{module}.gguf"},
    )
    monkeypatch.setattr(doctor.os.path, "exists", lambda path: True)

    check = doctor._check_models(SimpleNamespace())

    assert check.status == doctor.OK
    assert "multimodal" not in check.detail
