"""A drawing the model writes as text: hidden while it streams, stored afterwards."""

import pytest

from app.svg_answer import SvgBlockFilter, extract_svg_block, suggested_name

pytestmark = pytest.mark.unit


def test_prose_before_the_block_is_forwarded_and_the_block_is_not():
    filter_ = SvgBlockFilter()
    first = filter_.feed("Вот схема. ```svg\n<svg xmlns=")
    later = filter_.feed('http://www.w3.org/2000/svg"></svg>```')

    assert first == "Вот схема. "
    assert filter_.suppressed is True
    assert later == ""
    assert filter_.flush() == ""


def test_a_fence_split_across_chunks_is_still_caught():
    filter_ = SvgBlockFilter()
    filter_.feed("Сейчас нарисую. ``")
    assert filter_.suppressed is False

    filter_.feed("`svg\n<svg/>")

    assert filter_.suppressed is True


def test_plain_prose_passes_through_and_is_flushed():
    filter_ = SvgBlockFilter()
    text = "Готово, файл в документах."

    assert filter_.feed(text) + filter_.flush() == text
    assert filter_.suppressed is False


def test_a_fence_with_a_space_is_caught():
    filter_ = SvgBlockFilter()

    filter_.feed("``` svg\n<svg/>")

    assert filter_.suppressed is True


def test_the_block_is_extracted_with_its_markup():
    found = extract_svg_block('Держите:\n```svg\n<svg xmlns="x"><title>T</title></svg>\n```\n')

    assert found is not None
    start, end, markup = found
    assert markup.startswith("<svg")
    assert start < end


def test_the_name_comes_from_the_title():
    assert suggested_name('<svg><title>Пеликан на велосипеде</title></svg>', "Рисунок") == "Пеликан на велосипеде.svg"


def test_an_untitled_drawing_gets_the_fallback_name():
    assert suggested_name("<svg><circle/></svg>", "Рисунок") == "Рисунок.svg"


def test_a_title_cannot_carry_a_path():
    assert suggested_name('<svg><title>a/b:c</title></svg>', "x") == "a b c.svg"
