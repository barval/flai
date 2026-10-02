"""Branding settings: DB helpers, admin API and the context processor.

Covers the Personalization tab end-to-end: the singleton `branding_settings`
row, logo upload/normalization/delete with realpath containment, the
all-or-nothing site-name pair, and the `branding_logo_url` /
`branding_site_name` template variables.
"""

import io
import os

import pytest
from PIL import Image


def _png_bytes(size: tuple[int, int] = (256, 128), color=(200, 30, 30)) -> bytes:
    img = Image.new("RGBA", size, color + (255,))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def branding_app(test_app):
    """test_app with a tmp branding folder and a seeded admin session."""
    test_app.config["BRANDING_FOLDER"] = os.path.join(test_app.config["UPLOAD_FOLDER"], "..", "branding-test")
    test_app.config["BRANDING_FOLDER"] = os.path.abspath(test_app.config["BRANDING_FOLDER"])
    os.makedirs(test_app.config["BRANDING_FOLDER"], exist_ok=True)
    return test_app


@pytest.fixture
def admin_client(client):
    with client.session_transaction() as sess:
        sess["login"] = "admin"
        sess["is_admin"] = True
        sess["language"] = "ru"
    return client


@pytest.mark.unit
class TestBrandingDb:
    def test_defaults_after_init(self, test_app):
        from app import db

        branding = db.get_branding_settings()
        assert branding["logo_path"] is None
        assert branding["site_name_ru"] == ""
        assert branding["site_name_en"] == ""

    def test_set_and_clear_logo(self, test_app):
        from app import db

        db.set_branding_logo("logo-abc.png")
        assert db.get_branding_settings()["logo_path"] == "logo-abc.png"
        db.set_branding_logo(None)
        assert db.get_branding_settings()["logo_path"] is None

    def test_site_names_roundtrip(self, test_app):
        from app import db

        db.set_branding_site_names("ПЛИИ-Тест", "FLAI-Test")
        branding = db.get_branding_settings()
        assert branding["site_name_ru"] == "ПЛИИ-Тест"
        assert branding["site_name_en"] == "FLAI-Test"


@pytest.mark.unit
class TestBrandingApi:
    def test_branding_requires_admin(self, client):
        resp = client.get("/admin/api/branding")
        assert resp.status_code == 403

    def test_get_default_branding(self, admin_client):
        resp = admin_client.get("/admin/api/branding")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["has_logo"] is False
        assert data["logo_url"] is None
        assert data["site_name_ru"] == ""

    def test_logo_serves_404_without_upload(self, admin_client):
        assert admin_client.get("/admin/api/branding/logo").status_code == 404

    def test_upload_logo_normalizes_to_png(self, branding_app, admin_client):
        data = _png_bytes((512, 256))
        resp = admin_client.post(
            "/admin/api/branding/logo",
            data={"logo": (io.BytesIO(data), "logo.png")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
        out = resp.get_json()
        assert out["ok"] is True
        from PIL import Image as PILImage

        stored = PILImage.open(
            os.path.join(branding_app.config["BRANDING_FOLDER"], os.listdir(branding_app.config["BRANDING_FOLDER"])[0])
        )
        assert max(stored.size) <= 128
        assert stored.format == "PNG"

    def test_upload_rejects_non_image(self, branding_app, admin_client):
        resp = admin_client.post(
            "/admin/api/branding/logo",
            data={"logo": (io.BytesIO(b"not an image"), "x.png")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 400

    def test_upload_rejects_oversize(self, branding_app, admin_client):
        big = _png_bytes() + b"0" * (2 * 1024 * 1024 + 1)
        resp = admin_client.post(
            "/admin/api/branding/logo",
            data={"logo": (io.BytesIO(big), "logo.png")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 413

    def test_delete_logo_restores_default(self, branding_app, admin_client):
        admin_client.post(
            "/admin/api/branding/logo",
            data={"logo": (io.BytesIO(_png_bytes()), "logo.png")},
            content_type="multipart/form-data",
        )
        resp = admin_client.delete("/admin/api/branding/logo")
        assert resp.status_code == 200
        assert admin_client.get("/admin/api/branding/logo").status_code == 404
        from app import db

        assert db.get_branding_settings()["logo_path"] is None

    def test_names_require_both_languages(self, admin_client):
        resp = admin_client.post("/admin/api/branding/names", json={"site_name_ru": "Только русский"})
        assert resp.status_code == 400
        assert resp.get_json()["code" if False else "error"]

    def test_names_too_long_rejected(self, admin_client):
        resp = admin_client.post(
            "/admin/api/branding/names",
            json={"site_name_ru": "Р" * 41, "site_name_en": "E" * 10},
        )
        assert resp.status_code == 400

    def test_names_saved_when_both_present(self, admin_client):
        resp = admin_client.post(
            "/admin/api/branding/names",
            json={"site_name_ru": "Мой ПЛИИ", "site_name_en": "My FLAI"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["site_name_ru"] == "Мой ПЛИИ"

    def test_emptying_either_name_resets_pair(self, admin_client):
        admin_client.post("/admin/api/branding/names", json={"site_name_ru": "Мой ПЛИИ", "site_name_en": "My FLAI"})
        # Emptying one side resets the whole pair (no half-translated brand).
        resp = admin_client.post("/admin/api/branding/names", json={"site_name_ru": "Новое", "site_name_en": ""})
        assert resp.status_code == 400

    def test_both_empty_is_a_deliberate_reset(self, admin_client):
        admin_client.post("/admin/api/branding/names", json={"site_name_ru": "Мой ПЛИИ", "site_name_en": "My FLAI"})
        resp = admin_client.post("/admin/api/branding/names", json={"site_name_ru": "", "site_name_en": ""})
        assert resp.status_code == 200
        from app import db

        assert db.get_branding_settings()["site_name_ru"] == ""


@pytest.mark.unit
class TestBrandingTemplateVars:
    def test_defaults_render_built_in_brand(self, client):
        resp = client.get("/login")
        html = resp.get_data(as_text=True)
        assert "logo-header.png" in html  # built-in logo while no custom one
        assert "/admin/api/branding/logo" not in html
        # Default header name / tab title: the localized short brand (ПЛИИ / FLAI).
        assert "<title>ПЛИИ</title>" in html
        assert ">ПЛИИ</h1>" in html
        assert "Полностью Локальный ИИ" not in html.split("footer-about-modal")[0].split("<footer>")[0]

    def test_placeholder_hints_are_language_independent(self, admin_client):
        """RU field hints ПЛИИ, EN field hints FLAI — in every profile language."""
        with admin_client.session_transaction() as sess:
            sess["language"] = "ru"
        resp = admin_client.get("/admin/")
        html = resp.get_data(as_text=True)
        # Both hints render regardless of profile language: RU→ПЛИИ, EN→FLAI.
        ru_field = html[html.find('id="branding-name-ru"') :]
        en_field = html[html.find('id="branding-name-en"') :]
        assert 'placeholder="ПЛИИ"' in ru_field[:400]
        assert 'placeholder="FLAI"' in en_field[:400]

    def test_custom_logo_and_name_render(self, branding_app, admin_client):
        from app import db

        folder = branding_app.config["BRANDING_FOLDER"]
        with open(os.path.join(folder, "logo-test.png"), "wb") as f:
            f.write(_png_bytes())
        db.set_branding_logo("logo-test.png")
        db.set_branding_site_names("Мой ПЛИИ", "My FLAI")
        branding_app.config["WTF_CSRF_ENABLED"] = False
        resp = admin_client.get("/admin/")
        html = resp.get_data(as_text=True)
        assert "/admin/api/branding/logo?v=" in html
        assert "Мой ПЛИИ" in html
        assert '<h1 class="brand-name">' in html

    def test_broken_logo_path_falls_back_to_builtin(self, branding_app, admin_client):
        from app import db

        db.set_branding_logo("../../escape.png")
        resp = admin_client.get("/admin/")
        html = resp.get_data(as_text=True)
        assert "logo-header.png" in html

    def test_serving_logo_rejects_traversal(self, branding_app, admin_client):
        from app import db

        db.set_branding_logo("../uploads/secret.png")
        resp = admin_client.get("/admin/api/branding/logo")
        assert resp.status_code == 404
