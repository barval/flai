# tests/test_graph_prompt.py
"""The graph section of the reasoning prompt: content, cap, and absence."""

from modules.base import BaseModule


def _triple(index: int = 1) -> dict:
    return {
        "src": f"Альфа {index}",
        "dst": f"Бета {index}",
        "predicate": "поставляет",
        "doc_name": "договор.pdf",
    }


def test_the_section_names_the_relation_and_its_document():
    from app.graph_rag import format_graph_section

    text = format_graph_section([_triple()], [], 600)

    assert "поставляет" in text
    assert "Альфа 1" in text and "Бета 1" in text
    assert "договор.pdf" in text


def test_the_section_respects_its_token_cap():
    from app.graph_rag import format_graph_section

    text = format_graph_section([_triple(index) for index in range(200)], [], 600)

    assert len(text) <= 600 * 4


def test_an_empty_section_is_not_rendered(test_app):
    from app.utils import estimate_tokens

    with test_app.app_context():
        messages = BaseModule().build_reasoning_messages("вопрос", "10:00", "ru", "neutral", "контекст", "")
        rendered = BaseModule().build_reasoning_messages(
            "вопрос", "10:00", "ru", "neutral", "контекст", "Связи: Альфа —[поставляет]→ Бета"
        )

    assert "Связи: Альфа" not in messages[-1]["content"]
    assert "Связи: Альфа" in rendered[-1]["content"]
    assert estimate_tokens(rendered[-1]["content"]) > estimate_tokens(messages[-1]["content"])


def test_the_section_reaches_the_generated_prompt(test_app, monkeypatch):
    """The live path is the generator; the renderer alone being wired proves nothing."""
    from types import SimpleNamespace

    base = BaseModule()
    captured = {}

    def fake_build(query, time_str, lang, style, context, graph_section=""):
        captured["section"] = graph_section
        return [{"role": "system", "content": "rules"}, {"role": "user", "content": graph_section or "question"}]

    monkeypatch.setattr(base, "build_reasoning_messages", fake_build)
    monkeypatch.setattr(base, "_get_context_for_model", lambda *args, **kwargs: "context")
    monkeypatch.setattr(base, "_validate_final_prompt", lambda *args, **kwargs: "")
    monkeypatch.setattr(base, "llamacpp", SimpleNamespace(chat_stream=lambda *args, **kwargs: iter(["ok"])))

    with test_app.app_context():
        list(base.generate_reasoning_response_stream("вопрос", "10:00", graph_section="Альфа —[поставляет]→ Бета"))

    assert captured["section"] == "Альфа —[поставляет]→ Бета"
