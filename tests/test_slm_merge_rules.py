# tests/test_slm_merge_rules.py
"""Tests for rule-based fact merging (app/slm_merge.py)."""


class TestFastCleanup:
    """Test deterministic cleanup (existing fast_cleanup)."""

    def test_removes_short_facts(self):
        from app.slm_merge import fast_cleanup

        facts = [
            {"fact_id": "a", "content": "Hi"},
            {"fact_id": "b", "content": "Я работаю инженером"},
        ]
        to_delete, remaining = fast_cleanup(facts)
        assert "a" in to_delete
        assert len(remaining) == 1

    def test_removes_exact_duplicates(self):
        from app.slm_merge import fast_cleanup

        facts = [
            {"fact_id": "a", "content": "Я люблю Python"},
            {"fact_id": "b", "content": "Я люблю Python"},
        ]
        to_delete, remaining = fast_cleanup(facts)
        assert len(to_delete) == 1
        assert len(remaining) == 1

    def test_removes_case_insensitive_duplicates(self):
        from app.slm_merge import fast_cleanup

        facts = [
            {"fact_id": "a", "content": "Python是最好的"},
            {"fact_id": "b", "content": "python是最好的"},
        ]
        to_delete, remaining = fast_cleanup(facts)
        assert len(to_delete) == 1

    def test_removes_fragments(self):
        from app.slm_merge import fast_cleanup

        facts = [
            {"fact_id": "a", "content": "Я работаю инженером в Яндексе уже 5 лет"},
            {"fact_id": "b", "content": "Я работаю инженером"},
        ]
        to_delete, remaining = fast_cleanup(facts)
        assert "b" in to_delete
        assert len(remaining) == 1

    def test_preserves_unique_facts(self):
        from app.slm_merge import fast_cleanup

        facts = [
            {"fact_id": "a", "content": "Я люблю Python"},
            {"fact_id": "b", "content": "Я работаю инженером"},
            {"fact_id": "c", "content": "Мне 30 лет"},
        ]
        to_delete, remaining = fast_cleanup(facts)
        assert len(to_delete) == 0
        assert len(remaining) == 3

    def test_sorted_newest_first(self):
        from app.slm_merge import fast_cleanup

        facts = [
            {"fact_id": "a", "content": "Старый факт", "created_at": 100},
            {"fact_id": "b", "content": "Новый факт", "created_at": 200},
        ]
        _, remaining = fast_cleanup(facts)
        assert remaining[0]["fact_id"] == "b"


class TestEditDistanceMerge:
    """Test Levenshtein-based near-duplicate detection."""

    def test_removes_near_duplicates(self):
        from app.slm_merge import edit_distance_merge

        facts = [
            {"fact_id": "a", "content": "Я работаю инженером в Яндексе"},
            {"fact_id": "b", "content": "Я работаю инженером в Яндексе already"},  # different
            {"fact_id": "c", "content": "Я работаю инженером в Яндексе "},  # trailing space
        ]
        to_delete = edit_distance_merge(facts, threshold=0.25)
        # "a" and "c" are very similar
        assert len(to_delete) >= 1

    def test_preserves_different_facts(self):
        from app.slm_merge import edit_distance_merge

        facts = [
            {"fact_id": "a", "content": "Я люблю Python"},
            {"fact_id": "b", "content": "Я работаю инженером"},
        ]
        to_delete = edit_distance_merge(facts)
        assert len(to_delete) == 0

    def test_empty_input(self):
        from app.slm_merge import edit_distance_merge

        assert edit_distance_merge([]) == []
        assert edit_distance_merge([{"fact_id": "a", "content": "Hi"}]) == []


class TestFragmentMerge:
    """Test stricter substring fragment detection."""

    def test_removes_fragments(self):
        from app.slm_merge import fragment_merge

        facts = [
            {"fact_id": "a", "content": "Я работаю инженером в Яндексе уже 5 лет и занимаюсь backend разработкой"},
            {"fact_id": "b", "content": "Я работаю инженером в Яндексе уже 5 лет"},
        ]
        to_delete = fragment_merge(facts, ratio=0.7)
        assert "b" in to_delete

    def test_preserves_independent_facts(self):
        from app.slm_merge import fragment_merge

        facts = [
            {"fact_id": "a", "content": "Я люблю Python"},
            {"fact_id": "b", "content": "Я работаю инженером"},
        ]
        to_delete = fragment_merge(facts)
        assert len(to_delete) == 0


class TestTemporalDecay:
    """Test auto-archiving of old low-confidence facts."""

    def test_removes_old_low_confidence(self):
        from app.slm_merge import temporal_decay

        # Fact from 100 days ago with low score
        facts = [
            {"fact_id": "a", "content": "Старый факт", "created_at": "2025-01-01T00:00:00", "score": 0.2},
            {"fact_id": "b", "content": "Новый факт", "created_at": "2026-06-01T00:00:00", "score": 0.9},
        ]
        to_delete = temporal_decay(facts, decay_days=90, min_confidence=0.5)
        assert "a" in to_delete
        assert "b" not in to_delete

    def test_preserves_old_high_confidence(self):
        from app.slm_merge import temporal_decay

        facts = [
            {"fact_id": "a", "content": "Важный факт", "created_at": "2025-01-01T00:00:00", "score": 0.9},
        ]
        to_delete = temporal_decay(facts, decay_days=90, min_confidence=0.5)
        assert len(to_delete) == 0

    def test_preserves_new_facts(self):
        from app.slm_merge import temporal_decay

        facts = [
            {"fact_id": "a", "content": "Недавний факт", "created_at": "2026-06-15T00:00:00", "score": 0.3},
        ]
        to_delete = temporal_decay(facts, decay_days=90, min_confidence=0.5)
        assert len(to_delete) == 0

    def test_handles_missing_created_at(self):
        from app.slm_merge import temporal_decay

        facts = [
            {"fact_id": "a", "content": "Факт без даты", "score": 0.1},
        ]
        to_delete = temporal_decay(facts, decay_days=90, min_confidence=0.5)
        assert len(to_delete) == 0

    def test_handles_timestamp_int(self):
        import time

        from app.slm_merge import temporal_decay
        old_ts = int(time.time()) - (100 * 86400)  # 100 days ago
        facts = [
            {"fact_id": "a", "content": "Old fact", "created_at": old_ts, "score": 0.2},
        ]
        to_delete = temporal_decay(facts, decay_days=90, min_confidence=0.5)
        assert "a" in to_delete
