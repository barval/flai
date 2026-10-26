# tests/test_chunk_structured.py
"""Structure-aware chunking: one logical entry per chunk."""

import re

import pytest

from app.utils import chunk_text_structured

MONTH_START = re.compile(r"(?i)^(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)")

RESUME = """Иванов Иван Иванович
Опыт работы — 12 лет 4 месяца
### Ноябрь 2023 — ООО «Альфа» настоящее время Тверь, alfa.example/ 2 года 3 месяца
### Финансовый сектор • Банк ### Главный инженер по разработке
- Написание скриптов автоматизации сборки и деплоя (Jenkins, Groovy).
### Ноябрь 2022 — ООО «Альфа»
### Ноябрь 2023 Тверь, alfa.example/ 1 год 1 месяц
- Главный инженер по разработке.
### Ноябрь 2018 — ООО «Вектор»
### Ноябрь 2022 Тверь, vector.example 4 года 1 месяц
- Поддержка разработчиков.
"""


@pytest.mark.unit
def test_each_entry_keeps_its_dates_and_city():
    chunks = chunk_text_structured(RESUME, chunk_size=500, overlap=50)

    entries = [chunk for chunk in chunks if MONTH_START.match(chunk)]
    assert len(entries) == 3, chunks
    assert "Ноябрь 2023 — ООО «Альфа»" in entries[0] and "настоящее время" in entries[0]
    assert "Ноябрь 2022 — ООО «Альфа»" in entries[1]
    assert "Ноябрь 2023 Тверь" in entries[1], "the closing date line stays with its entry"
    assert "ООО «Вектор»" in entries[-1] and "vector.example" in entries[-1]


@pytest.mark.unit
def test_two_entries_never_share_a_chunk():
    chunks = chunk_text_structured(RESUME, chunk_size=2000, overlap=50)

    for chunk in chunks:
        assert chunk.count(" — ") <= 1, f"two entries in one chunk: {chunk[:80]}"


@pytest.mark.unit
def test_a_long_block_falls_back_to_the_recursive_splitter():
    chat = "\n".join(f"сообщение номер {index} " + "текст " * 40 for index in range(50))

    chunks = chunk_text_structured(chat, chunk_size=500, overlap=50)

    assert len(chunks) > 1
    assert all(len(chunk) <= 700 for chunk in chunks)


@pytest.mark.unit
def test_empty_text_yields_no_chunks():
    assert chunk_text_structured("", 500, 50) == []


@pytest.mark.unit
def test_year_ranges_start_a_new_entry():
    text = "2018 — 2022 ООО Ромашка\nТверь\n2013 — 2018 ООО Лютик\nПсков"

    chunks = chunk_text_structured(text, 500, 50)

    assert len(chunks) == 2
    assert chunks[0].startswith("2018") and chunks[1].startswith("2013")
