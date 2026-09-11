# tests/test_slm_recall.py
"""
Tests for SLM hybrid recall helpers (services/superlocalmemory/slm_http.py).

Covers: _tokenize, _keyword_score, _recency_boost, _time_window_from_query,
        _hybrid_recall_from_user_db.
"""

import os
import sqlite3
import sys
import time
from unittest.mock import patch

import pytest

# Add slm_http.py to import path (lives in services/superlocalmemory/)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "superlocalmemory"))


# ── _tokenize ──────────────────────────────────────────────────────────


class TestTokenize:
    def test_ru_basic(self):
        from slm_http import _tokenize

        tokens = _tokenize("Мне нравится моя собака Рекс")
        assert "собака" in tokens
        assert "рекс" in tokens

    def test_ru_stopwords_removed(self):
        from slm_http import _tokenize

        tokens = _tokenize("Я работаю программистом в Google")
        assert "программистом" in tokens
        assert "google" in tokens
        assert "я" not in tokens
        assert "в" not in tokens

    def test_en_basic(self):
        from slm_http import _tokenize

        tokens = _tokenize("I love my cat Whiskers")
        assert "love" in tokens
        assert "cat" in tokens
        assert "whiskers" in tokens
        assert "my" not in tokens
        assert "i" not in tokens

    def test_short_tokens_excluded(self):
        from slm_http import _tokenize

        tokens = _tokenize("кот и я O X")
        # "кот" = 3 chars ✓, "и" = stopword, "я" = stopword, "O"/"X" = 1 char
        assert tokens == ["кот"]

    def test_numbers_kept(self):
        from slm_http import _tokenize

        tokens = _tokenize("Встреча 15 сентября")
        assert "15" in tokens
        assert "сентября" in tokens

    def test_empty_string(self):
        from slm_http import _tokenize

        assert _tokenize("") == []


# ── _keyword_score ────────────────────────────────────────────────────


class TestKeywordScore:
    def test_perfect_match(self):
        from slm_http import _keyword_score

        assert _keyword_score(["собака", "рекс"], "Моя собака Рекс — лабрадор") == 1.0

    def test_partial_match(self):
        from slm_http import _keyword_score

        score = _keyword_score(["собака", "рекс", "гулять"], "Моя собака Рекс")
        assert abs(score - 2 / 3) < 0.01

    def test_no_match(self):
        from slm_http import _keyword_score

        assert _keyword_score(["привет"], "Моя собака Рекс") == 0.0

    def test_empty_tokens(self):
        from slm_http import _keyword_score

        assert _keyword_score([], "любой текст") == 0.0

    def test_case_insensitive(self):
        from slm_http import _keyword_score

        assert _keyword_score(["собака"], "Моя СОБАКА Рекс") == 1.0


# ── _recency_boost ────────────────────────────────────────────────────


class TestRecencyBoost:
    def test_new_fact(self):
        from slm_http import _recency_boost

        now = int(time.time())
        boost = _recency_boost(now - 3600)  # 1 hour ago
        assert boost > 1.2

    def test_old_fact(self):
        from slm_http import _recency_boost

        now = int(time.time())
        boost = _recency_boost(now - 365 * 86400)  # 1 year ago
        assert 1.0 <= boost < 1.1

    def test_zero_timestamp(self):
        from slm_http import _recency_boost

        assert _recency_boost(0) == 1.0

    def test_none_timestamp(self):
        from slm_http import _recency_boost

        assert _recency_boost(None) == 1.0


# ── _time_window_from_query ───────────────────────────────────────────


class TestTimeWindow:
    def test_yesterday(self):
        from slm_http import _time_window_from_query

        window = _time_window_from_query("что я делал вчера")
        assert window is not None
        start, end = window
        assert end - start == 86400
        assert end < int(time.time())

    def test_today(self):
        from slm_http import _time_window_from_query

        window = _time_window_from_query("сегодня")
        assert window is not None
        start, end = window
        assert end - start <= 86400
        assert end - start > 0

    def test_this_week_ru(self):
        from slm_http import _time_window_from_query

        window = _time_window_from_query("на этой неделе")
        assert window is not None
        start, end = window
        assert 6 * 86400 <= end - start <= 7 * 86400

    def test_last_week_ru(self):
        from slm_http import _time_window_from_query

        window = _time_window_from_query("на прошлой неделе")
        assert window is not None
        start, end = window
        assert 6 * 86400 <= end - start <= 7 * 86400
        assert end < int(time.time()) - 6 * 86400

    def test_recently(self):
        from slm_http import _time_window_from_query

        window = _time_window_from_query("недавно")
        assert window is not None
        start, end = window
        assert end - start == 30 * 86400

    def test_english_recently(self):
        from slm_http import _time_window_from_query

        window = _time_window_from_query("recently I went hiking")
        assert window is not None

    def test_no_temporal_cue(self):
        from slm_http import _time_window_from_query

        assert _time_window_from_query("как зовут мою собаку") is None


# ── _hybrid_recall_from_user_db ───────────────────────────────────────


@pytest.fixture()
def user_db(tmp_path):
    """Create a minimal per-user SLM SQLite database with test facts."""
    profile = "test_user"
    db_dir = tmp_path / profile / ".superlocalmemory"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE atomic_facts ("
        "  fact_id TEXT PRIMARY KEY,"
        "  content TEXT,"
        "  confidence REAL,"
        "  created_at INTEGER,"
        "  lifecycle TEXT DEFAULT 'active'"
        ")"
    )
    conn.execute("CREATE TABLE memories (memory_id TEXT PRIMARY KEY)")

    now = int(time.time())
    facts = [
        ("f1", "Моя собака Рекс — лабрадор", 0.8, now - 86400 * 10),
        ("f2", "Я работаю программистом в Google", 0.9, now - 86400 * 5),
        ("f3", "Вчера я ходил в кино на фильм Дюна", 0.7, now - 86400),
        ("f4", "Мой любимый цвет — синий", 0.6, now - 86400 * 30),
        ("f5", "У меня есть кошка Мурка", 0.85, now - 86400 * 2),
    ]
    for fid, content, conf, ts in facts:
        conn.execute(
            "INSERT INTO atomic_facts VALUES (?, ?, ?, ?, 'active')",
            (fid, content, conf, ts),
        )
    conn.commit()
    conn.close()
    return {"path": db_path, "profile": profile, "tmp_path": tmp_path}


class TestHybridRecall:
    @pytest.fixture(autouse=True)
    def _patch_user_db(self, user_db):
        with patch("slm_http._user_db_path") as mock_path:
            mock_path.return_value = str(user_db["path"])
            yield

    def test_keyword_hit_returns_results(self, user_db):
        from slm_http import _hybrid_recall_from_user_db

        with patch("slm_http._semantic_recall_from_user_db") as mock_sem:
            results = _hybrid_recall_from_user_db("собака Рекс", 5, user_db["profile"])
            assert results is not None
            assert len(results) > 0
            assert any("Рекс" in r["content"] for r in results)
            mock_sem.assert_not_called()

    def test_keyword_miss_triggers_semantic(self, user_db):
        from slm_http import _hybrid_recall_from_user_db

        fake_result = [{"content": "Из daemon", "score": 0.8, "confidence": 0.7, "fact_id": "d1", "created_at": 1000}]
        with patch("slm_http._semantic_recall_from_user_db", return_value=fake_result) as mock_sem:
            results = _hybrid_recall_from_user_db("xyz_unknown_query", 5, user_db["profile"])
            mock_sem.assert_called_once()
            assert results == fake_result

    def test_falls_back_to_latest_when_all_miss(self, user_db):
        from slm_http import _hybrid_recall_from_user_db

        with patch("slm_http._semantic_recall_from_user_db", return_value=None):
            results = _hybrid_recall_from_user_db("xyz_unknown", 5, user_db["profile"])
            assert results is not None
            assert len(results) > 0

    def test_keyword_match_google(self, user_db):
        from slm_http import _hybrid_recall_from_user_db

        with patch("slm_http._semantic_recall_from_user_db"):
            results = _hybrid_recall_from_user_db("Google", 5, user_db["profile"])
            assert results is not None
            assert any("Google" in r["content"] for r in results)

    def test_nonexistent_profile_returns_none_when_no_data(self):
        """No user DB and no daemon available → None (route returns [])."""
        from slm_http import _hybrid_recall_from_user_db

        with patch("slm_http._user_db_path", return_value=None):
            assert _hybrid_recall_from_user_db("test", 5, "no_such_user") is None

    def test_nonexistent_profile_falls_back_to_daemon(self):
        """When the per-user DB is missing, fall back to daemon semantic recall."""
        from slm_http import _hybrid_recall_from_user_db

        fake_result = [{"content": "Из daemon", "score": 0.8, "confidence": 0.7, "fact_id": "d1", "created_at": 1000}]
        with (
            patch("slm_http._user_db_path", return_value=None),
            patch("slm_http._semantic_recall_from_user_db", return_value=fake_result) as mock_sem,
        ):
            results = _hybrid_recall_from_user_db("anything", 5, "no_such_user")
            mock_sem.assert_called_once()
            assert results == fake_result

    def test_nonexistent_profile_falls_back_to_latest(self):
        """If the daemon returns nothing too, fall back to latest per-user facts."""
        from slm_http import _hybrid_recall_from_user_db

        fake_result = [{"content": "latest", "score": 0.5, "confidence": 0.5, "fact_id": "l1", "created_at": 1000}]
        with (
            patch("slm_http._user_db_path", return_value=None),
            patch("slm_http._semantic_recall_from_user_db", return_value=None),
            patch("slm_http._recall_from_user_db", return_value=fake_result) as mock_latest,
        ):
            results = _hybrid_recall_from_user_db("anything", 5, "no_such_user")
            mock_latest.assert_called_once()
            assert results == fake_result
