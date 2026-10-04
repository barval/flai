# tests/test_crawl_task.py
"""The crawl→document→RAG→answer pipeline with a mocked container."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from app import queue as queue_mod


class _FakeBase:
    """Minimal stand-in for the base module: echoes the msgid, formatted.

    A MagicMock cannot translate or apply the kwargs the crawl task passes to
    ``_(msgid, lang, **kwargs)``, so the real stub returns a predictable string.
    """

    def _(self, msgid, lang=None, **kwargs):
        return msgid.format(**kwargs) if kwargs else msgid

    def get_search_context_limit(self):
        return 10000


def _queue(mock_app, mock_redis, crawler):
    q = queue_mod.RedisRequestQueue.__new__(queue_mod.RedisRequestQueue)
    q.app = mock_app
    q.logger = mock_app.logger
    q._publish_stream_event = MagicMock()
    q._build_error_response = MagicMock(return_value={"status": "error"})
    q._requeue_reasoning_task = MagicMock()
    crawler_module = MagicMock()
    crawler_module.crawl_site.return_value = [
        {"url": "https://docs.example.com/a", "markdown": "Alpha page"},
        {"url": "https://docs.example.com/b", "markdown": "Beta page"},
    ]
    mock_app.modules = {"crawler": crawler_module, "base": _FakeBase()}
    mock_app.config["DOCUMENTS_FOLDER"] = "/tmp/flai-test-docs"
    return q, crawler_module


def _crawl_task(task_id="t1", **data):
    payload = {"type": "crawl_task", "query": "q", "url": "https://docs.example.com", "response_style": "neutral"}
    payload.update(data)
    return {"task_id": task_id, "user_id": "valery", "session_id": "s1", "lang": "ru", "data": payload}


@pytest.mark.unit
class TestCrawlTask:
    @pytest.fixture
    def mock_app(self):
        app = MagicMock()
        app.config = {"REDIS_URL": "redis://localhost:6379/0", "SECRET_KEY": "test-secret-key"}
        app.logger = MagicMock()
        return app

    @pytest.fixture
    def mock_redis(self):
        redis = Mock()
        redis.blpop.return_value = None
        redis.llen.return_value = 1
        pipe = Mock()
        pipe.execute.return_value = []
        redis.pipeline.return_value = pipe
        return redis

    def test_classified_to_dedicated_executor(self, mock_app, mock_redis):
        assert queue_mod.RedisRequestQueue._classify_task(MagicMock(), {"type": "crawl_task"}) == "crawl"

    def test_crawl_task_never_loads_a_gpu_model(self, mock_app, mock_redis):
        """The crawl worker takes no GPU lock, so the task must map to no model."""
        q = queue_mod.RedisRequestQueue.__new__(queue_mod.RedisRequestQueue)
        q.app = mock_app
        assert q._get_model_for_task({"data": {"type": "crawl_task"}}) == "none"

    def test_crawl_action_is_enqueued_to_the_crawl_queue(self, mock_app, mock_redis):
        """The router's crawl action must reach the crawl executor, never run inline."""
        base = Mock()
        base.process_message.return_value = {
            "action": "crawl",
            "query": "изучи https://docs.example.com",
            "needs_reasoning": True,
        }
        mock_app.modules = {"base": base}
        with (
            patch("app.queue.RedisRequestQueue.start_worker"),
            patch("app.queue.redis.from_url", return_value=mock_redis),
        ):
            queue = queue_mod.RedisRequestQueue(mock_app)
        queue._publish_stream_event = Mock()
        queue._process_crawl_task = Mock()

        result = queue._route_text_action(
            "изучи https://docs.example.com", "s1", "valery", "2026-10-04 12:00:00", "ru", "neutral", 2, None, True
        )

        queue._process_crawl_task.assert_not_called()
        assert result["status"] == "queued"
        pushed_key = mock_redis.pipeline.return_value.rpush.call_args[0][0]
        assert pushed_key == queue.crawl_queue_key
        assert pushed_key not in (queue.queue_key, queue.slow_queue_key)

    def test_full_pipeline(self, mock_app, mock_redis, tmp_path):
        q, crawler = _queue(mock_app, mock_redis, None)
        mock_app.config["DOCUMENTS_FOLDER"] = str(tmp_path)
        mock_app.modules["rag"] = MagicMock(available=True)
        with (
            patch("app.queue.db") as db,
            patch("app.queue.check_document_quota", return_value=None),
            patch("app.queue._run_document_indexing") as run_indexing,
        ):
            db.INDEX_STATUS_PENDING = "pending"
            db.save_document.side_effect = lambda *a, **k: "doc-1"
            db.get_user_documents.return_value = []

            q._process_crawl_task(_crawl_task("t1", query="изучи docs.example.com"))
            requeue_kwargs = q._requeue_reasoning_task.call_args.kwargs

        assert requeue_kwargs.get("rag_source") == "crawler"
        assert "docs.example.com" in requeue_kwargs.get("rag_context", "")
        assert requeue_kwargs.get("rag_context", "").count("## ") == 2
        crawler.crawl_site.assert_called_once()
        db.save_document.assert_called_once()
        run_indexing.assert_called_once()
        q._requeue_reasoning_task.assert_called_once()

    def test_domain_replacement_deletes_previous_document(self, mock_app, mock_redis, tmp_path):
        q, _crawler = _queue(mock_app, mock_redis, None)
        mock_app.config["DOCUMENTS_FOLDER"] = str(tmp_path)
        mock_app.modules["rag"] = MagicMock(available=True)
        with (
            patch("app.queue.db") as db,
            patch("app.queue.check_document_quota", return_value=None),
            patch("app.queue._run_document_indexing"),
        ):
            db.INDEX_STATUS_PENDING = "pending"
            db.get_user_documents.return_value = [
                {"id": "old-doc", "filename": "docs.example.com", "file_path": "valery/old.txt"}
            ]
            q._process_crawl_task(_crawl_task("t2"))
            db.delete_document.assert_called_once_with("old-doc", "valery")

    def test_quota_full_is_a_localized_error(self, mock_app, mock_redis, tmp_path):
        q, _crawler = _queue(mock_app, mock_redis, None)
        mock_app.config["DOCUMENTS_FOLDER"] = str(tmp_path)
        with (
            patch("app.queue.check_document_quota", return_value="⚠️ quota"),
            patch("app.queue.db") as db,
        ):
            result = q._process_crawl_task(_crawl_task("t3"))
            db.save_document.assert_not_called()

        assert result["status"] == "error"
        assert "⚠️" in result["response"]

    def test_quota_filled_during_crawl_saves_nothing(self, mock_app, mock_redis, tmp_path):
        """A quota filled during the multi-minute crawl must not leave a stored document."""
        q, _crawler = _queue(mock_app, mock_redis, None)
        mock_app.config["DOCUMENTS_FOLDER"] = str(tmp_path)
        mock_app.modules["rag"] = MagicMock(available=True)
        with (
            patch("app.queue.db") as db,
            patch("app.queue.check_document_quota", side_effect=[None, "⚠️ quota"]),
            patch("app.queue._run_document_indexing") as run_indexing,
        ):
            q._process_crawl_task(_crawl_task("t3b"))
            db.save_document.assert_not_called()

        # The localized quota error is surfaced, and nothing was stored or re-queued.
        error_arg = q._build_error_response.call_args[0][1]
        assert error_arg == "Document quota reached — the collected materials were not saved"
        run_indexing.assert_not_called()
        q._requeue_reasoning_task.assert_not_called()

    def test_zero_pages_is_a_soft_localized_error(self, mock_app, mock_redis, tmp_path):
        q, crawler = _queue(mock_app, mock_redis, None)
        mock_app.config["DOCUMENTS_FOLDER"] = str(tmp_path)
        crawler.crawl_site.return_value = []
        with patch("app.queue.db"), patch("app.queue.check_document_quota", return_value=None):
            result = q._process_crawl_task(_crawl_task("t4"))

        assert result["status"] == "error"
        assert result["response"] == "Could not read any pages from docs.example.com"
        q._requeue_reasoning_task.assert_not_called()

    def test_blocked_url_is_a_localized_error(self, mock_app, mock_redis, tmp_path):
        from app.crawler_guard import BlockedUrlError

        q, crawler = _queue(mock_app, mock_redis, None)
        mock_app.config["DOCUMENTS_FOLDER"] = str(tmp_path)
        crawler.crawl_site.side_effect = BlockedUrlError("URL resolves to a non-public address")
        with patch("app.queue.db"), patch("app.queue.check_document_quota", return_value=None):
            result = q._process_crawl_task(_crawl_task("t5"))

        assert result["status"] == "error"
        q._requeue_reasoning_task.assert_not_called()

    def test_missing_url_is_a_localized_error(self, mock_app, mock_redis, tmp_path):
        q, crawler = _queue(mock_app, mock_redis, None)
        mock_app.config["DOCUMENTS_FOLDER"] = str(tmp_path)
        with patch("app.queue.db"):
            result = q._process_crawl_task(_crawl_task("t6", url="", query="изучи сайт"))

        assert result["status"] == "error"
        crawler.crawl_site.assert_not_called()
        q._requeue_reasoning_task.assert_not_called()

    def test_extract_first_url(self):
        extract = queue_mod.RedisRequestQueue._extract_first_url
        assert extract("изучи https://docs.example.com/guide.") == "https://docs.example.com/guide"
        assert extract("вот http://a.b/c и https://d.e") == "http://a.b/c"
        assert extract("ссылки нет") == ""


@pytest.mark.unit
class TestCrawlErrorMessages:
    """Crawl error strings shown to the user must carry the ⚠️ prefix."""

    @pytest.mark.parametrize(
        "msgid",
        [
            "Crawler service is unavailable",
            "Could not read any pages from {domain}",
            "Document quota reached — the collected materials were not saved",
        ],
    )
    def test_ru_translation_starts_with_warning(self, msgid):
        with open("translations/ru/LC_MESSAGES/messages.po", encoding="utf-8") as po:
            content = po.read()
        assert f'msgid "{msgid}"' in content
        entry = content.split(f'msgid "{msgid}"', 1)[1].split("\n\n", 1)[0]
        assert 'msgstr "⚠️ ' in entry
