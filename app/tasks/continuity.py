# app/tasks/continuity.py
"""Pure-logic helpers for the visual continuity sheet ("visual bible") of Tasks.

No Flask app, no DB, no LLM calls — deterministic text processing only:

- ``parse_bible``: extract and schema-validate the continuity sheet from LLM prose.
- ``select_cards``: match continuity cards against arbitrary texts (substring,
  then lemma via pymorphy3, case-insensitive).
- ``render_sheet``: compact text sheet injected into image-generation prompts.
- ``sanitize_reference_markers``: strip source-reference artifacts (line ranges,
  source pointers) left by the extractor, keeping scene numbering and quotes.
"""

import json
import re

from app.morph import _get_morph

# Cards are selected from the combined set of characters, places and props.
_CARD_KEYS = ("characters", "places", "props")

# Sanitizer patterns. Removing the parenthesized marker first leaves an empty
# "(){}" pair behind; the follow-up pattern deletes it together with any space
# before it (so "Scene 8 ():" becomes "Scene 8:").
_PAREN_MARKER_RE = re.compile(
    r"\((?:строк\w*|lines?|источник|source)[^)]{0,80}\)", re.IGNORECASE)
_EMPTY_PARENS_RE = re.compile(r"\s*\(\s*\)\s*")
_BARE_MARKER_RE = re.compile(
    r"(?:\bстрок\w*|\blines?\b|стр\.)\s*~?\s*\d+(?:\s*[–—-]\s*~?\s*\d+)?",
    re.IGNORECASE)
_DOUBLE_SPACE_RE = re.compile(r"  +")


def sanitize_reference_markers(text: str) -> str:
    """Remove source-reference artifacts (line ranges, source pointers).

    Strips parenthesized or bare markers like ``(строки ~540–621)``,
    ``(lines 100-150)``, ``строка 42``, ``line ~7``, ``стр. 3``,
    ``(источник: ...)`` / ``(source: ...)``; keeps scene numbering and quoted
    phrases; removes an enclosing orphan parenthesis pair left empty and
    collapses doubled spaces. Applied repeatedly until the text is stable.
    """
    result = text
    while True:
        updated = _PAREN_MARKER_RE.sub(" ", result)
        updated = _BARE_MARKER_RE.sub(" ", updated)
        updated = _EMPTY_PARENS_RE.sub("", updated)
        updated = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", updated)
        updated = _DOUBLE_SPACE_RE.sub(" ", updated)
        if updated == result:
            break
        result = updated
    return result.strip()


def parse_bible(raw: str) -> dict | None:
    """Extract a continuity sheet from LLM prose containing a JSON object.

    Uses the JSON-from-prose slicing pattern of the replan path (first ``{``
    ... last ``}``) and validates the schema: ``style`` is a string and
    ``characters``/``places``/``props`` are lists of ``{id, aliases, look}``.
    Returns the schema-normalized dict, or None when no valid sheet is found.
    """
    if not isinstance(raw, str) or "{" not in raw or "}" not in raw:
        return None
    try:
        data = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    style = data.get("style")
    if not isinstance(style, str):
        style = ""
    cards: dict[str, list] = {}
    for key in _CARD_KEYS:
        entries: list = []
        raw_list = data.get(key)
        if isinstance(raw_list, list):
            for entry in raw_list:
                if not isinstance(entry, dict):
                    continue
                card_id = entry.get("id")
                aliases = entry.get("aliases")
                look = entry.get("look")
                if not isinstance(card_id, str) or not card_id.strip():
                    continue
                if not isinstance(aliases, list) \
                        or not all(isinstance(a, str) for a in aliases):
                    continue
                if not isinstance(look, str):
                    look = ""
                entries.append({"id": card_id, "aliases": aliases, "look": look})
        cards[key] = entries
    return {"style": style, **cards}


def select_cards(bible: dict, *texts: str) -> list[dict]:
    """Select continuity cards mentioned in any of the texts.

    Matching is case-insensitive: a fast substring pass first, then a lemma
    pass via pymorphy3 («Разговор сов» matches alias «сова» through the
    analyzer's normal forms). Deduplicates by card id.
    """
    joined = " ".join(filter(None, texts)).lower()
    if not joined.strip():
        return []
    tokens = re.findall(r"\w+", joined, re.UNICODE)
    morph = _get_morph()

    selected: list[dict] = []
    seen: set[str] = set()
    for key in _CARD_KEYS:
        for card in bible.get(key, []):
            card_id = card.get("id")
            if not isinstance(card_id, str) or card_id in seen:
                continue
            if _card_matches(card, joined, tokens, morph):
                seen.add(card_id)
                selected.append(card)
    return selected


def _card_matches(card: dict, joined: str, tokens: list[str], morph) -> bool:
    """Check whether any alias (or the card id) occurs in the joined text."""
    candidates = list(card.get("aliases") or []) + [card.get("id")]
    alias_lemmas: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        lowered = candidate.strip().lower()
        if lowered in joined:
            return True
        alias_lemmas.extend(_normal_forms(morph, lowered))
    for token in tokens:
        token_lemmas = _normal_forms(morph, token)
        if token in alias_lemmas or token_lemmas & set(alias_lemmas):
            return True
    return False


def _normal_forms(morph, word: str) -> set[str]:
    """Return the analyzer's normal forms for a word across all parses (≥ the word itself)."""
    forms = {word}
    try:
        parsed = morph.parse(word)
    except Exception:
        return forms
    for entry in parsed:
        if entry.normal_form:
            forms.add(entry.normal_form)
    return forms


# Spaces before punctuation introduced by marker removal (e.g. "Scene 8 :")
# are closed back up.
_SPACE_BEFORE_PUNCT_RE = re.compile(r" +([,.;:!?])")


def render_sheet(bible: dict, goal: str = "", criteria: str = "") -> str:
    """Render the compact continuity sheet for an image-generation prompt.

    Format: the fixed header, then ``Style: <style>`` (only when non-empty),
    then one ``<Id>: <look>`` line per card selected over ``goal``/``criteria``
    (nothing selected → style-only output). The caller applies the token budget.
    """
    lines = ["CONTINUITY (mandatory for this task, keep identical across all images):"]
    style = bible.get("style") or ""
    if style.strip():
        lines.append(f"Style: {style.strip()}")
    for card in select_cards(bible, goal, criteria):
        card_id = card['id']
        display_id = card_id[:1].upper() + card_id[1:]
        lines.append(f"{display_id}: {card.get('look', '')}")
    return "\n".join(lines)
