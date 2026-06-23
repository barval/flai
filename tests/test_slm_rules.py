# tests/test_slm_rules.py
"""Tests for rule-based fact extraction (app/slm_rules.py)."""



class TestSplitSentences:
    """Test sentence splitting logic."""

    def test_split_simple(self):
        from app.slm_rules import _split_sentences

        result = _split_sentences("Я работаю инженером. У меня есть кот. Люблю программирование.")
        assert len(result) == 3

    def test_split_respects_abbreviations(self):
        from app.slm_rules import _split_sentences

        result = _split_sentences("Он из г. Москва. Работает в ООО Ромашка.")
        assert len(result) >= 2

    def test_split_skips_short(self):
        from app.slm_rules import _split_sentences

        result = _split_sentences("Да. Нет. Ок.")
        assert len(result) == 0

    def test_split_empty(self):
        from app.slm_rules import _split_sentences

        assert _split_sentences("") == []
        assert _split_sentences("   ") == []


class TestScoreSentence:
    """Test sentence scoring by category patterns."""

    def test_preference_ru(self):
        from app.slm_rules import _score_sentence

        score, category = _score_sentence("Мне нравится программировать на Python", "ru")
        assert score >= 0.4
        assert category == "preference"

    def test_fact_ru(self):
        from app.slm_rules import _score_sentence

        score, category = _score_sentence("Я работаю инженером в Москве уже пять лет", "ru")
        assert score >= 0.4
        assert category == "fact"

    def test_instruction_ru(self):
        from app.slm_rules import _score_sentence

        score, category = _score_sentence("Всегда отвечай на русском языке", "ru")
        assert score >= 0.4
        assert category == "instruction"

    def test_person_ru(self):
        from app.slm_rules import _score_sentence

        score, category = _score_sentence("Мою дочь зовут Алиса, ей 5 лет", "ru")
        assert score >= 0.3
        assert category in ("personality", "context", "fact")

    def test_preference_en(self):
        from app.slm_rules import _score_sentence

        score, category = _score_sentence("I love working with Python and machine learning", "en")
        assert score >= 0.4
        assert category == "preference"

    def test_fact_en(self):
        from app.slm_rules import _score_sentence

        score, category = _score_sentence("I work as a software engineer at Google", "en")
        assert score >= 0.4
        assert category == "fact"

    def test_instruction_en(self):
        from app.slm_rules import _score_sentence

        score, category = _score_sentence("Always answer in English and keep it concise", "en")
        assert score >= 0.4
        assert category == "instruction"

    def test_low_score_for_generic(self):
        from app.slm_rules import _score_sentence

        score, _ = _score_sentence("The weather is nice today", "en")
        assert score < 0.4

    def test_question_penalty(self):
        from app.slm_rules import _score_sentence

        score_q, _ = _score_sentence("Какой сегодня день?", "ru")
        score_s, _ = _score_sentence("Сегодня понедельник", "ru")
        assert score_q < score_s


class TestNormalizeText:
    """Test text normalization for comparison."""

    def test_lowercase(self):
        from app.slm_rules import _normalize_text

        assert _normalize_text("Hello World") == "hello world"

    def test_strip_punctuation(self):
        from app.slm_rules import _normalize_text

        assert _normalize_text("Hello, world!") == "hello world"

    def test_normalize_spaces(self):
        from app.slm_rules import _normalize_text

        assert _normalize_text("  hello   world  ") == "hello world"


class TestLevenshteinRatio:
    """Test Levenshtein similarity computation."""

    def test_identical(self):
        from app.slm_rules import _levenshtein_ratio

        assert _levenshtein_ratio("hello", "hello") == 1.0

    def test_empty(self):
        from app.slm_rules import _levenshtein_ratio

        assert _levenshtein_ratio("", "hello") == 0.0
        assert _levenshtein_ratio("hello", "") == 0.0

    def test_similar(self):
        from app.slm_rules import _levenshtein_ratio

        ratio = _levenshtein_ratio("hello world", "hello worlds")
        assert ratio > 0.9

    def test_different(self):
        from app.slm_rules import _levenshtein_ratio

        ratio = _levenshtein_ratio("hello", "cat")
        assert ratio < 0.5

    def test_length_difference_optimization(self):
        from app.slm_rules import _levenshtein_ratio

        ratio = _levenshtein_ratio("a", "abcdefghij")
        assert ratio == 0.0


class TestExtractFacts:
    """Test main extract_facts function."""

    def test_skip_commands(self):
        from app.slm_rules import extract_facts

        result = extract_facts("сделай мне сайт", "Создаю сайт с нуля.", [], lang="ru")
        assert result == []

    def test_skip_short_query(self):
        from app.slm_rules import extract_facts

        result = extract_facts("привет", "Привет! Чем могу помочь?", [], lang="ru")
        assert result == []

    def test_skip_short_response(self):
        from app.slm_rules import extract_facts

        result = extract_facts("расскажи о себе", "OK", [], lang="ru")
        assert result == []

    def test_extract_preference(self):
        from app.slm_rules import extract_facts

        query = "Мне нравится Python. Какой язык лучше?"
        response = "Отлично! Python — отличный выбор для старта."
        result = extract_facts(query, response, [], lang="ru", max_facts=3)
        assert len(result) >= 1
        assert any("Python" in f["text"] or "нравится" in f["text"] for f in result)

    def test_extract_fact(self):
        from app.slm_rules import extract_facts

        query = "Я работаю инженером в компании Яндекс уже 3 года и занимаюсь разработкой."
        response = "Понял, спасибо за информацию!"
        result = extract_facts(query, response, [], lang="ru", max_facts=3)
        assert len(result) >= 1
        assert any("инженер" in f["text"] or "работаю" in f["text"] for f in result)

    def test_deduplication(self):
        from app.slm_rules import extract_facts

        existing = [{"text": "Да, я работаю инженером в компании Яндекс уже 3 года."}]
        response = "Да, я работаю инженером в компании Яндекс уже 3 года."
        result = extract_facts("Где работаешь?", response, existing, lang="ru")
        assert len(result) == 0

    def test_max_facts_limit(self):
        from app.slm_rules import extract_facts

        response = (
            "Мне нравится Python. Я люблю JavaScript. Я предпочитаю Rust. "
            "Мой любимый язык — Go. Я обожаю C++."
        )
        result = extract_facts("Что你喜欢?", response, [], lang="ru", max_facts=2)
        assert len(result) <= 2

    def test_category_assignment(self):
        from app.slm_rules import extract_facts

        query = "Всегда используй формат JSON для ответов. Это важно для парсинга."
        response = "Хорошо, буду использовать JSON."
        result = extract_facts(query, response, [], lang="ru", max_facts=3)
        assert any(f["category"] == "instruction" for f in result)


class TestExtractFromRemember:
    """Test explicit 'remember' extraction."""

    def test_ru_prefix_pomni(self):
        from app.slm_rules import extract_from_remember

        result = extract_from_remember("Помни, что мой день рождения 15 марта", "ru")
        assert len(result) == 1
        assert "15 марта" in result[0]

    def test_ru_prefix_zapomni(self):
        from app.slm_rules import extract_from_remember

        result = extract_from_remember("Запомни: я работаю в Яндексе", "ru")
        assert len(result) == 1
        assert "Яндексе" in result[0]

    def test_en_prefix_remember_that(self):
        from app.slm_rules import extract_from_remember

        result = extract_from_remember("Remember that I prefer dark mode", "en")
        assert len(result) == 1
        assert "dark mode" in result[0]

    def test_en_prefix_remember_colon(self):
        from app.slm_rules import extract_from_remember

        result = extract_from_remember("Remember: my birthday is March 15", "en")
        assert len(result) == 1
        assert "March 15" in result[0]

    def test_fallback_full_text(self):
        from app.slm_rules import extract_from_remember

        result = extract_from_remember("My birthday is March 15", "en")
        assert len(result) == 1
        assert "March 15" in result[0]

    def test_empty_query(self):
        from app.slm_rules import extract_from_remember

        assert extract_from_remember("", "ru") == []
        assert extract_from_remember("   ", "en") == []

    def test_truncation(self):
        from app.slm_rules import extract_from_remember

        long_text = "Помни, что " + "x" * 300
        result = extract_from_remember(long_text, "ru")
        assert len(result) == 1
        assert len(result[0]) <= 200
