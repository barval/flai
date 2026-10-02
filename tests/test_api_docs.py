"""Interactive API documentation: Swagger UI over a hand-written OpenAPI spec.

The spec is maintained by hand in `docs/openapi-v1.yaml` and served by
flasgger, which bundles the Swagger UI assets so the page works offline. The
drift guard below fails the build when a `/v1` route exists without a spec
entry, so documentation cannot silently fall behind the implementation.
"""

import re

import pytest


def _is_doc_route(rule: str) -> bool:
    """Documentation endpoints are part of the surface but not of the API contract."""
    return rule == "/v1/openapi.json" or rule.startswith("/v1/docs")


@pytest.mark.unit
class TestApiDocs:
    def test_swagger_ui_is_served_without_a_cdn(self, client):
        response = client.get("/v1/docs")

        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "swagger-ui" in html
        assert "unpkg.com" not in html
        assert "cdn.jsdelivr.net" not in html
        assert "fonts.googleapis.com" not in html
        # No remote asset may be fetched: the UI must work offline. Plain
        # <a href> attribution links load nothing, so they are not assets.
        loaded = [
            url
            for tag in re.findall(r"<(?:script|link|img)\b[^>]*>", html)
            for url in re.findall(r'(?:src|href)="(https?://[^"]+)"', tag)
        ]
        assert not loaded, f"Swagger UI loads remote assets: {loaded}"

    def test_spec_endpoint_serves_a_valid_openapi_document(self, client):
        response = client.get("/v1/openapi.json")

        assert response.status_code == 200
        spec = response.get_json()
        assert spec["openapi"].startswith("3.")
        assert spec["info"]["title"]
        assert spec["paths"]

    def test_spec_declares_bearer_authentication_globally(self, client):
        spec = client.get("/v1/openapi.json").get_json()

        scheme = spec["components"]["securitySchemes"]["bearerAuth"]
        assert scheme["type"] == "http"
        assert scheme["scheme"] == "bearer"
        assert spec["security"] == [{"bearerAuth": []}]

    def test_spec_documents_every_registered_v1_route(self, client, test_app):
        spec = client.get("/v1/openapi.json").get_json()
        documented = {re.sub(r"\{(\w+)\}", r"<\1>", path) for path in spec["paths"]}
        registered = {rule.rule for rule in test_app.url_map.iter_rules() if rule.rule.startswith("/v1")}

        undocumented = sorted(rule for rule in registered - documented if not _is_doc_route(rule))
        assert not undocumented, f"/v1 routes missing from docs/openapi-v1.yaml: {undocumented}"
        assert not documented - registered, (
            f"Spec documents routes that do not exist: {sorted(documented - registered)}"
        )

    def test_spec_is_served_under_the_project_spec_file(self, client):
        spec = client.get("/v1/openapi.json").get_json()

        assert "/v1/chat/completions" in spec["paths"]
        assert "/v1/flai/chat/async" in spec["paths"]
        assert "/v1/flai/tasks/{task_id}" in spec["paths"]
