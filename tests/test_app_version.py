"""The application version must have exactly one source in code."""

import re
import tomllib
from pathlib import Path

import pytest

from app.config import APP_VERSION


@pytest.mark.unit
class TestAppVersion:
    def test_config_exposes_the_module_constant(self, test_app):
        assert test_app.config["APP_VERSION"] == APP_VERSION
        assert re.fullmatch(r"\d+\.\d+", APP_VERSION)

    def test_metrics_report_the_configured_version(self, client):
        body = client.get("/metrics").get_data(as_text=True)

        assert f'flai_web_info{{version="{APP_VERSION}"}} 1' in body

    def test_version_matches_pyproject(self):
        pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

        assert ".".join(pyproject["project"]["version"].split(".")[:2]) == APP_VERSION
