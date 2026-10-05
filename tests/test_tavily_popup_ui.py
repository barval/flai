"""Tests for the Tavily section inside the profile popup."""

import re

import pytest

from app.userdb import create_user


@pytest.fixture
def popup_client(client, test_app):
    create_user(login="popupuser", password="pw-popupuser-12345", name="Popup User")
    with client.session_transaction() as browser_session:
        browser_session["login"] = "popupuser"
        browser_session["user_id"] = "popupuser"
        browser_session["name"] = "Popup User"
    return client


@pytest.mark.unit
class TestTavilyPopupMarkup:
    def test_section_precedes_the_flai_api_keys_block(self, popup_client):
        html = popup_client.get("/chat").get_data(as_text=True)

        assert 'id="tavily-section"' in html
        assert 'id="tavily-key-input"' in html
        assert 'id="tavily-key-add-btn"' in html
        assert 'id="tavily-key-del-btn"' in html
        assert html.index('id="tavily-section"') < html.index('class="api-keys-header"')

    def test_registration_link_points_at_tavily(self, popup_client):
        html = popup_client.get("/chat").get_data(as_text=True)

        assert 'href="https://app.tavily.com/home"' in html
        assert 'rel="noopener noreferrer"' in html

    def test_api_keys_block_is_renamed_to_the_project_brand(self, popup_client):
        with popup_client.session_transaction() as browser_session:
            browser_session["language"] = "ru"
        ru_html = popup_client.get("/chat").get_data(as_text=True)
        with popup_client.session_transaction() as browser_session:
            browser_session["language"] = "en"
        en_html = popup_client.get("/chat").get_data(as_text=True)

        assert re.search(r'class="api-keys-header">\s*<span>ПЛИИ API ключи</span>', ru_html)
        assert re.search(r'class="api-keys-header">\s*<span>FLAI API keys</span>', en_html)

    def test_tavily_script_is_loaded(self, popup_client):
        html = popup_client.get("/chat").get_data(as_text=True)

        assert "js/tavily-key.js" in html
