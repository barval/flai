# tests/test_crawl_task.py
"""The crawl→document→RAG→answer pipeline with a mocked container."""

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from app import queue as queue_mod

_REPO_ROOT = Path(__file__).resolve().parents[1]
_INDEX_OK = (True, "indexed", "embed-model", False)


def queue_mod_site_blocked(url: str, max_pages: int | None = None, max_depth: int | None = None) -> list[dict]:
    """crawl_site stand-in that raises the anti-bot error."""
    from modules.crawler import SiteBlockedError

    raise SiteBlockedError("Blocked by anti-bot protection")


class _FakeBase:
    """Minimal stand-in for the base module: echoes the msgid, formatted.

    A MagicMock cannot translate or apply the kwargs the crawl task passes to
    ``_(msgid, lang, **kwargs)``, so the real stub returns a predictable string.
    """

    def _(self, msgid, lang=None, **kwargs):
        return msgid.format(**kwargs) if kwargs else msgid

    def get_search_context_limit(self):
        return 10000


class _RecordingLock:
    """Stand-in for ``_gpu_lock`` that records when it is held."""

    def __init__(self, log):
        self.log = log

    def __enter__(self):
        self.log.append("lock-enter")
        return self

    def __exit__(self, *exc):
        self.log.append("lock-exit")
        return False


def _queue(app):
    """A crawl worker queue bound to `app`, with a recording GPU lock."""
    q = queue_mod.RedisRequestQueue.__new__(queue_mod.RedisRequestQueue)
    q.app = app
    q.logger = app.logger
    q._publish_stream_event = MagicMock()
    q._requeue_reasoning_task = MagicMock()
    events = []
    q._gpu_lock = _RecordingLock(events)
    q._cleanup_vram_after_task = MagicMock(side_effect=lambda task: events.append("cleanup"))
    crawler_module = MagicMock()
    crawler_module.crawl_site.return_value = [
        {"url": "https://docs.example.com/a", "markdown": "Alpha page"},
        {"url": "https://docs.example.com/b", "markdown": "Beta page"},
    ]
    app.modules = {"crawler": crawler_module, "base": _FakeBase()}
    app.config["DOCUMENTS_FOLDER"] = "/tmp/flai-test-docs"
    return q, crawler_module


def _quota_db(documents, queries=None):
    """A get_db() context manager that applies the quota query's own filters.

    ``documents`` are the user's rows as (id, file_size) pairs, so the optional
    ``id <> %s`` exclusion really removes the to-be-replaced document from the
    counted rows instead of being asserted on the SQL text alone.
    """
    cursor = MagicMock()
    cursor.fetchone.return_value = {"count": 0, "coalesce": 0}

    def execute(sql, params=None):
        normalized = " ".join(sql.split())
        if queries is not None:
            queries.append((normalized, params))
        rows = [d for d in documents if not ("id <> %s" in normalized and d[0] == params[1])]
        cursor.fetchone.return_value = {"count": len(rows), "coalesce": sum(d[1] for d in rows)}

    cursor.execute.side_effect = execute
    conn = MagicMock()
    conn.cursor.return_value = cursor
    ctx = MagicMock()
    ctx.__enter__.return_value = conn
    ctx.__exit__.return_value = False
    return ctx


def _crawl_task(task_id="t1", **data):
    payload = {"type": "crawl_task", "query": "q", "url": "https://docs.example.com", "response_style": "neutral"}
    payload.update(data)
    return {"task_id": task_id, "user_id": "valery", "session_id": "s1", "lang": "ru", "data": payload}


@pytest.fixture(autouse=True)
def saved_messages():
    """Capture the assistant message an error response persists."""
    with patch("app.db.save_message", return_value=99) as save:
        yield save


def _saved_text(save_message):
    """The single content argument passed to save_message."""
    assert save_message.call_count == 1
    args = save_message.call_args.args
    assert args[1] == "assistant"
    return args[2]


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
        """The crawl worker never loads a model; only the indexing phase uses the GPU."""
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

    def test_crawl_action_takes_the_url_from_the_user_message(self, mock_app, mock_redis):
        """The address comes from the user's text, not from the router's rewrite."""
        base = Mock()
        base.process_message.return_value = {
            "action": "crawl",
            "query": "изучи документацию на сайте",
            "needs_reasoning": True,
        }
        mock_app.modules = {"base": base}
        with (
            patch("app.queue.RedisRequestQueue.start_worker"),
            patch("app.queue.redis.from_url", return_value=mock_redis),
        ):
            queue = queue_mod.RedisRequestQueue(mock_app)

        queue._route_text_action(
            "изучи https://docs.example.com/guide",
            "s1",
            "valery",
            "2026-10-04 12:00:00",
            "ru",
            "neutral",
            2,
            None,
            True,
        )

        queued = queue._deserialize(mock_redis.pipeline.return_value.rpush.call_args[0][1])
        assert queued["data"]["url"] == "https://docs.example.com/guide"
        # The router's query stays the reasoning handoff.
        assert queued["data"]["query"] == "изучи документацию на сайте"

    def test_full_pipeline(self, mock_app, mock_redis, tmp_path):
        q, crawler = _queue(mock_app)
        mock_app.config["DOCUMENTS_FOLDER"] = str(tmp_path)
        mock_app.modules["rag"] = MagicMock(available=True)
        with (
            patch("app.queue.db") as db,
            patch("app.queue.check_document_quota", return_value=None),
            patch("app.queue.update_document_index_status"),
            patch("app.queue.get_current_time_for_db", return_value="2026-10-04 12:00:00"),
            patch("app.queue._run_document_indexing", return_value=_INDEX_OK) as run_indexing,
        ):
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

    def test_indexing_runs_serialized_on_the_gpu_lock_then_frees_vram(self, mock_app, tmp_path):
        """Indexing embeds chunks on the GPU, so it runs under the global GPU lock."""
        q, _crawler = _queue(mock_app)
        mock_app.config["DOCUMENTS_FOLDER"] = str(tmp_path)
        mock_app.modules["rag"] = MagicMock(available=True)
        events = q._gpu_lock.log

        def _index(*args, **kwargs):
            events.append("index")
            return _INDEX_OK

        with (
            patch("app.queue.db") as db,
            patch("app.queue.check_document_quota", return_value=None),
            patch("app.queue.update_document_index_status"),
            patch("app.queue.get_current_time_for_db", return_value="2026-10-04 12:00:00"),
            patch("app.queue._run_document_indexing", side_effect=_index),
        ):
            db.get_user_documents.return_value = []
            task = _crawl_task("t-lock")
            q._process_crawl_task(task)

        assert events == ["lock-enter", "index", "cleanup", "lock-exit"]
        q._cleanup_vram_after_task.assert_called_once_with(task)

    def test_domain_replacement_deletes_previous_document(self, mock_app, mock_redis, tmp_path):
        q, _crawler = _queue(mock_app)
        mock_app.config["DOCUMENTS_FOLDER"] = str(tmp_path)
        mock_app.modules["rag"] = MagicMock(available=True)
        with (
            patch("app.queue.db") as db,
            patch("app.queue.check_document_quota", return_value=None),
            patch("app.queue.update_document_index_status"),
            patch("app.queue.get_current_time_for_db", return_value="2026-10-04 12:00:00"),
            patch("app.queue._run_document_indexing", return_value=_INDEX_OK),
        ):
            db.get_user_documents.return_value = [
                {"id": "old-doc", "filename": "docs.example.com", "file_path": "valery/old.txt"}
            ]
            q._process_crawl_task(_crawl_task("t2"))
            db.delete_document.assert_called_once_with("old-doc", "valery")

    def test_quota_full_is_a_persisted_localized_error(self, mock_app, saved_messages, tmp_path):
        q, _crawler = _queue(mock_app)
        mock_app.config["DOCUMENTS_FOLDER"] = str(tmp_path)
        with (
            patch("app.queue.check_document_quota", return_value="Document quota exceeded: 50 / 50 documents."),
            patch("app.queue.db") as db,
        ):
            db.get_user_documents.return_value = []
            result = q._process_crawl_task(_crawl_task("t3"))
            db.save_document.assert_not_called()

        assert result["is_error"] is True
        assert result["error"] == "⚠️ Document quota exceeded: 50 / 50 documents."
        assert _saved_text(saved_messages) == result["error"]

    def test_quota_filled_during_crawl_saves_nothing(self, mock_app, saved_messages, tmp_path):
        """A quota filled during the multi-minute crawl must not leave a stored document."""
        q, _crawler = _queue(mock_app)
        mock_app.config["DOCUMENTS_FOLDER"] = str(tmp_path)
        mock_app.modules["rag"] = MagicMock(available=True)
        with (
            patch("app.queue.db") as db,
            patch("app.queue.check_document_quota", side_effect=[None, "quota exceeded mid-crawl"]),
            patch("app.queue.update_document_index_status"),
            patch("app.queue.get_current_time_for_db", return_value="2026-10-04 12:00:00"),
            patch("app.queue._run_document_indexing", return_value=_INDEX_OK) as run_indexing,
        ):
            db.get_user_documents.return_value = []
            q._process_crawl_task(_crawl_task("t3b"))
            db.save_document.assert_not_called()

        # The localized quota error is persisted, and nothing was stored or re-queued.
        assert _saved_text(saved_messages) == "⚠️ Document quota reached — the collected materials were not saved"
        run_indexing.assert_not_called()
        q._requeue_reasoning_task.assert_not_called()

    def test_crawl_context_never_exceeds_the_search_limit(self, mock_app):
        """A single 50k-char page must not blow the reasoning prompt.

        Regression: the old first-page exception accepted a page larger than
        the whole context budget (50035 chars → 25802 estimated tokens vs the
        23347-token limit → "Request too long" after every github.com crawl).
        """
        q, _ = _queue(mock_app)
        limit = mock_app.modules["base"].get_search_context_limit()  # 10000 in the fake
        pages = [
            {"url": "https://site.com/a", "markdown": "x" * 50000},
            {"url": "https://site.com/b", "markdown": "y" * 50000},
        ]
        context = q._crawl_context(pages)
        assert len(context) <= limit + 10  # + header/section overhead
        # Deterministic prefix: the first page fills the budget, the second is cut off.
        assert context.startswith("## https://site.com/a")
        assert "https://site.com/b" not in context

    def test_crawl_context_splits_budget_across_pages(self, mock_app):
        q, _ = _queue(mock_app)
        pages = [
            {"url": "https://site.com/a", "markdown": "a" * 6000},
            {"url": "https://site.com/b", "markdown": "b" * 6000},
            {"url": "https://site.com/c", "markdown": "c" * 6000},
        ]
        context = q._crawl_context(pages)
        # 10000-char budget: page a (6000) + page b truncated to ~4000; page c cut off.
        assert "https://site.com/a" in context
        assert "https://site.com/b" in context
        assert "https://site.com/c" not in context
        assert len(context) <= 10000 + 10

    def test_site_blocked_falls_back_to_ordinary_search(self, mock_app, mock_redis):
        """Anti-bot block is not a dead end: the task falls back to a web search."""
        q, crawler = _queue(mock_app)
        crawler.crawl_site.side_effect = queue_mod_site_blocked
        with (
            patch("app.queue.db"),
            patch("app.queue.check_document_quota", return_value=None),
            patch.object(queue_mod.RedisRequestQueue, "_process_search_task") as search_mock,
        ):
            search_mock.return_value = {"status": "queued", "task_id": "search-1"}
            q._process_crawl_task(_crawl_task("t-sb", query="изучи https://blocked.example.com"))

        search_mock.assert_called_once()
        kwargs = search_mock.call_args.kwargs
        # The reasoning model is told WHY the crawl failed.
        assert "anti-bot" in (kwargs.get("reasoning_query") or "")
        assert kwargs.get("task") is not None
        # No document stored, no reasoning requeue from the crawl itself.
        q._requeue_reasoning_task.assert_not_called()

    def test_zero_pages_is_a_persisted_soft_error(self, mock_app, saved_messages, tmp_path):
        q, crawler = _queue(mock_app)
        mock_app.config["DOCUMENTS_FOLDER"] = str(tmp_path)
        crawler.crawl_site.return_value = []
        with (
            patch("app.queue.db") as db,
            patch("app.queue.check_document_quota", return_value=None),
        ):
            db.get_user_documents.return_value = []
            result = q._process_crawl_task(_crawl_task("t4"))

        assert result["is_error"] is True
        assert result["error"].count("⚠️") == 1
        assert "docs.example.com" in result["error"]
        assert _saved_text(saved_messages) == result["error"]
        q._requeue_reasoning_task.assert_not_called()

    def test_blocked_url_is_a_localized_error(self, mock_app, tmp_path):
        from app.crawler_guard import BlockedUrlError

        q, crawler = _queue(mock_app)
        mock_app.config["DOCUMENTS_FOLDER"] = str(tmp_path)
        crawler.crawl_site.side_effect = BlockedUrlError("URL resolves to a non-public address")
        with (
            patch("app.queue.db") as db,
            patch("app.queue.check_document_quota", return_value=None),
        ):
            db.get_user_documents.return_value = []
            result = q._process_crawl_task(_crawl_task("t5"))

        assert result["is_error"] is True
        q._requeue_reasoning_task.assert_not_called()

    def test_missing_url_is_not_reported_as_a_service_failure(self, mock_app, saved_messages, tmp_path):
        q, crawler = _queue(mock_app)
        mock_app.config["DOCUMENTS_FOLDER"] = str(tmp_path)
        with patch("app.queue.db"):
            result = q._process_crawl_task(_crawl_task("t6", url="", query="изучи сайт"))

        assert result["error"] == "⚠️ No link found in your message"
        assert "unavailable" not in result["error"]
        crawler.crawl_site.assert_not_called()
        q._requeue_reasoning_task.assert_not_called()
        assert _saved_text(saved_messages) == "⚠️ No link found in your message"

    def test_extract_first_url(self):
        extract = queue_mod.RedisRequestQueue._extract_first_url
        assert extract("изучи https://docs.example.com/guide.") == "https://docs.example.com/guide"
        assert extract("вот http://a.b/c и https://d.e") == "http://a.b/c"
        assert extract("ссылки нет") == ""


@pytest.mark.unit
class TestCrawlQuotaAgainstReplacement:
    """A re-crawl replaces the domain document, so the quota must not count it."""

    QUOTA = 50

    def _run(self, test_app, existing_docs):
        test_app.config["MAX_DOCUMENTS_PER_USER"] = self.QUOTA
        q, _crawler = _queue(test_app)
        queries = []
        rows = [(existing_docs[0]["id"], 1024)] * self.QUOTA
        with (
            patch("app.database.get_db", return_value=_quota_db(rows, queries)),
            patch("app.queue.db") as db,
            patch("app.queue._run_document_indexing", return_value=_INDEX_OK),
        ):
            db.get_user_documents.return_value = existing_docs
            with test_app.app_context():
                result = q._process_crawl_task(_crawl_task("q1"))
        return q, result, db, queries

    def test_recrawl_at_the_quota_boundary_proceeds(self, test_app):
        q, _result, db, queries = self._run(
            test_app,
            [{"id": "old-doc", "filename": "docs.example.com", "file_path": "valery/old.txt"}],
        )

        # Both quota checks leave the replaced document out of count and size.
        assert [params for _, params in queries] == [("valery", "old-doc")] * 2
        assert all("id <> %s" in sql for sql, _ in queries)
        db.delete_document.assert_called_once_with("old-doc", "valery")
        db.save_document.assert_called_once()
        q._requeue_reasoning_task.assert_called_once()

    def test_new_domain_at_the_quota_boundary_is_refused(self, test_app, saved_messages):
        q, result, db, queries = self._run(
            test_app,
            [{"id": "other-doc", "filename": "other.example.com", "file_path": "valery/other.txt"}],
        )

        # No document of this domain to replace, so nothing is excluded.
        assert all("id <> %s" not in sql for sql, _ in queries)
        assert [params for _, params in queries] == [("valery",)]
        db.save_document.assert_not_called()
        q._requeue_reasoning_task.assert_not_called()
        assert result["is_error"] is True
        assert _saved_text(saved_messages).count("⚠️") == 1


@pytest.mark.unit
class TestCrawlErrorMessages:
    """Crawl error strings shown to the user must carry exactly one ⚠️ prefix."""

    CRAWL_ERRORS = [
        "Crawler service is unavailable",
        "No link found in your message",
        "Could not read any pages from {domain}",
        "Document quota reached — the collected materials were not saved",
        "Failed to save the collected materials",
        "This address is not available for reading",
    ]

    @pytest.mark.parametrize("lang", ["ru", "en"])
    @pytest.mark.parametrize("msgid", CRAWL_ERRORS)
    def test_catalog_translation_has_exactly_one_warning_prefix(self, msgid, lang):
        po_path = _REPO_ROOT / "translations" / lang / "LC_MESSAGES" / "messages.po"
        with open(po_path, encoding="utf-8") as po:
            content = po.read()
        assert f'msgid "{msgid}"' in content
        entry = content.split(f'msgid "{msgid}"', 1)[1].split("\n\n", 1)[0]
        msgstr = entry.split('msgstr "', 1)[1].rsplit('"', 1)[0]
        assert msgstr.startswith("⚠️ ")
        assert msgstr.count("⚠️") == 1

    @pytest.mark.parametrize("lang", ["ru", "en"])
    @pytest.mark.parametrize("msgid", CRAWL_ERRORS)
    def test_rendered_error_has_exactly_one_warning_prefix(self, msgid, lang, test_app):
        """Real compiled catalog + the real builder — the prefix is added once."""
        q = queue_mod.RedisRequestQueue.__new__(queue_mod.RedisRequestQueue)
        q.app = test_app
        q.logger = test_app.logger
        with test_app.app_context():
            translated = test_app.modules["base"]._(msgid, lang, domain="example.com")
        rendered = q._build_error_response("s1", translated, 0, lang)["error"]
        assert translated != msgid  # the compiled catalog really translated it
        assert rendered.count("⚠️") == 1
