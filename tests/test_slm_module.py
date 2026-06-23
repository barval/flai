# tests/test_slm_module.py
"""Tests for SlmModule (SuperLocalMemory long-term memory)."""

from unittest.mock import MagicMock, patch

import pytest


class TestSlmModuleInit:
    """Test SlmModule initialization."""

    @pytest.fixture
    def mock_app(self):
        app = MagicMock()
        app.config = {
            "SLM_URL": "http://flai-slm:8766",
            "SLM_RECALL_LIMIT": 7,
        }
        app.logger = MagicMock()
        return app

    def test_init_without_url(self):
        from modules.slm import SlmModule

        module = SlmModule()
        assert module.url is None
        assert not module.available

    def test_init_with_url_unavailable(self, mock_app):
        with patch("modules.slm.requests.get") as mock_get:
            mock_get.side_effect = Exception("Connection refused")
            from modules.slm import SlmModule

            module = SlmModule(mock_app)
            assert module.url == "http://flai-slm:8766"
            assert not module.available

    def test_init_with_url_available(self, mock_app):
        with patch("modules.slm.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response
            from modules.slm import SlmModule

            module = SlmModule(mock_app)
            assert module.available


class TestSlmModuleOperations:
    """Test SlmModule remember/recall/get_context."""

    @pytest.fixture
    def module(self):
        with patch("modules.slm.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response
            from modules.slm import SlmModule

            app = MagicMock()
            app.config = {"SLM_URL": "http://flai-slm:8766", "SLM_RECALL_LIMIT": 7}
            app.logger = MagicMock()
            mod = SlmModule(app)
            return mod

    def test_remember_success(self, module):
        with patch("modules.slm.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            result = module.remember("User likes cats")
            assert result

    def test_remember_failure(self, module):
        with patch("modules.slm.requests.post") as mock_post:
            mock_post.side_effect = Exception("Timeout")
            result = module.remember("User likes cats")
            assert not result

    def test_recall_success(self, module):
        with patch("modules.slm.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "data": {
                    "results": [
                        {"text": "User likes cats", "score": 0.95},
                        {"text": "User prefers dogs", "score": 0.82},
                    ]
                }
            }
            mock_post.return_value = mock_response
            results = module.recall("pets")
            assert len(results) == 2
            assert results[0]["text"] == "User likes cats"

    def test_recall_failure(self, module):
        with patch("modules.slm.requests.post") as mock_post:
            mock_post.side_effect = Exception("Timeout")
            results = module.recall("pets")
            assert results == []

    def test_recall_not_available(self, module):
        module.available = False
        results = module.recall("pets")
        assert results == []

    def test_remember_with_profile(self, module):
        with patch("modules.slm.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            result = module.remember("User likes dogs", profile="alice")
            assert result
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["profile"] == "alice"

    def test_recall_with_profile(self, module):
        with patch("modules.slm.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"data": {"results": [{"text": "User likes cats", "score": 0.95}]}}
            mock_post.return_value = mock_response
            results = module.recall("pets", profile="bob")
            assert len(results) == 1
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["profile"] == "bob"

    def test_forget_success(self, module):
        with patch("modules.slm.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            result = module.forget("old memories")
            assert result

    def test_forget_failure(self, module):
        with patch("modules.slm.requests.post") as mock_post:
            mock_post.side_effect = Exception("Timeout")
            result = module.forget("old memories")
            assert not result

    def test_forget_not_available(self, module):
        module.available = False
        result = module.forget("old memories")
        assert not result

    def test_forget_with_profile(self, module):
        with patch("modules.slm.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            result = module.forget("old memories", profile="charlie")
            assert result
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["profile"] == "charlie"

    def test_list_facts_success(self, module):
        with patch("modules.slm.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "data": {
                    "results": [
                        {"content": "Fact one", "fact_id": "abc123"},
                        {"content": "Fact two", "fact_id": "def456"},
                    ]
                }
            }
            mock_post.return_value = mock_response
            results = module.list_facts(limit=5)
            assert len(results) == 2
            assert results[0]["content"] == "Fact one"

    def test_list_facts_failure(self, module):
        with patch("modules.slm.requests.post") as mock_post:
            mock_post.side_effect = Exception("Timeout")
            results = module.list_facts()
            assert results == []

    def test_list_facts_not_available(self, module):
        module.available = False
        results = module.list_facts()
        assert results == []

    def test_list_facts_with_profile(self, module):
        with patch("modules.slm.requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"data": {"results": []}}
            mock_post.return_value = mock_response
            results = module.list_facts(profile="dave")
            assert results == []
            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["json"]["profile"] == "dave"
