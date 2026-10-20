# app/tasks/continuity.py
"""Pure-logic helpers for the visual continuity sheet ("visual bible") of Tasks.

No Flask app, no DB, no LLM calls — deterministic text processing only:

- ``parse_bible``: extract and schema-validate the continuity sheet from LLM prose.
- ``select_cards``: match continuity cards against arbitrary texts (substring,
  then lemma via pymorphy3, case-insensitive; multi-word aliases match
  word-wise by lemma).
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
    r"\((?:строк\w*|lines?|источник|source)[^)]{0,200}\)", re.IGNORECASE)
_EMPTY_PARENS_RE = re.compile(r"\s*\(\s*\)\s*")
_BARE_MARKER_RE = re.compile(
    r"(?:\bстрок\w*|\blines?\b|стр\.)\s*~?\s*\d+(?:\s*[–—-]\s*~?\s*\d+)?",
    re.IGNORECASE)
_DOUBLE_SPACE_RE = re.compile(r"  +")


def sanitize_reference_markers(text: str) -> str:
    """Remove source-reference artifacts (line ranges, source pointers).

    Strips parenthesized or bare markers like ``(строки ~540–621)``,
    ``(lines 100-150)``, ``строка 42``, ``line ~7``, ``стр. 3``;
    citation payloads up to 200 chars inside ``(источник: ...)`` /
    ``(source: ...)`` are removed. Keeps scene numbering and quoted
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
    analyzer's normal forms). Multi-word aliases match word-wise: every
    alias word's normal forms must be present among the text-token lemmas
    (set containment over all parses, contiguity not required), so
    «Нарисуй Бабу Ягу» matches alias «Баба Яга». Deduplicates by card id.
    """
    joined = " ".join(filter(None, texts)).lower()
    if not joined.strip():
        return []
    tokens = re.findall(r"\w+", joined, re.UNICODE)
    morph = _get_morph()
    token_forms = [_normal_forms(morph, t) for t in tokens]
    text_lemmas: set[str] = set().union(*token_forms) if token_forms else set()

    selected: list[dict] = []
    seen: set[str] = set()
    for key in _CARD_KEYS:
        for card in bible.get(key, []):
            card_id = card.get("id")
            if not isinstance(card_id, str) or card_id in seen:
                continue
            if _card_matches(card, joined, token_forms, text_lemmas, morph):
                seen.add(card_id)
                selected.append(card)
    return selected


def _card_matches(card: dict, joined: str, token_forms: list[set[str]],
                  text_lemmas: set[str], morph) -> bool:
    """Check whether any alias (or the card id) occurs in the joined text."""
    candidates = list(card.get("aliases") or []) + [card.get("id")]
    alias_lemmas: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        lowered = candidate.strip().lower()
        if lowered in joined:
            return True
        words = re.findall(r"\w+", lowered, re.UNICODE)
        if len(words) > 1:
            # Multi-word alias: every word's lemma must be present among the
            # text-token lemmas (case handled by the lowered join above).
            if all(_normal_forms(morph, w) & text_lemmas for w in words):
                return True
        else:
            alias_lemmas |= _normal_forms(morph, lowered)
    return any(forms & alias_lemmas for forms in token_forms)


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

    Format: the fixed header, ``Style: <style>`` (only when non-empty), then
    one grouped line per entity class — ``Characters:`` / ``Places:`` /
    ``Props:`` — with the cards selected over ``goal``/``criteria``. The
    grouping keeps entities from being lost inside one flat list; nothing
    selected → style-only output. The caller applies the token budget.
    """
    lines = ["CONTINUITY (mandatory for this task, keep identical across all images):"]
    style = bible.get("style") or ""
    if style.strip():
        lines.append(f"Style: {style.strip()}")
    selected = select_cards(bible, goal, criteria)
    selected_ids = {str(c.get("id") or "") for c in selected}
    for key, label in (("characters", "Characters"), ("places", "Places"), ("props", "Props")):
        cards = [c for c in bible.get(key, []) if str(c.get("id") or "") in selected_ids]
        if not cards:
            continue
        parts = []
        for card in cards:
            display_id = str(card.get("id") or "")
            display_id = display_id[:1].upper() + display_id[1:]
            parts.append(f"{display_id}: {card.get('look', '')}")
        lines.append(f"{label}: " + "; ".join(parts))
    return "\n".join(lines)


def goal_signature(text: str) -> frozenset[str]:
    """Comparable word-lemma set of a plan goal (order/punctuation ignored).

    Used to treat a paraphrase («Нарисуй сцену 3» / «Нарисовать сцену 3») as
    the same plan step instead of a new one.
    """
    tokens = re.findall(r"\w+", str(text or "").lower(), re.UNICODE)
    if not tokens:
        return frozenset()
    morph = _get_morph()
    forms: set[str] = set()
    for token in tokens:
        forms |= _normal_forms(morph, token)
    return frozenset(forms)


def select_relevant_fragment(text: str, query: str = "", criteria: str = "", cap: int = 4000) -> str:
    """The part of a long source result that the step's goal/criteria name.

    The verifier used to receive the whole multi-part source (up to 12k chars)
    while the step drew ONE part, so it judged the picture against the other
    scenes too. Paragraphs are ranked by lemma overlap with goal+criteria and
    the best ones are returned up to ``cap``; no overlap falls back to the head.
    """
    body = str(text or "").strip()
    if not body:
        return ""
    query_sig = goal_signature(" ".join(filter(None, (query, criteria))))
    if not query_sig:
        return body[:cap]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if len(paragraphs) <= 1:
        return body[:cap]
    scored = []
    for index, paragraph in enumerate(paragraphs):
        overlap = len(goal_signature(paragraph) & query_sig)
        if overlap:
            scored.append((overlap, index, paragraph))
    if not scored:
        return body[:cap]
    scored.sort(key=lambda item: (-item[0], item[1]))
    chosen: list[tuple[int, str]] = []
    used = 0
    for _score, index, paragraph in scored:
        if chosen and used + len(paragraph) + 2 > cap:
            continue
        chosen.append((index, paragraph))
        used += len(paragraph) + 2
        if used >= cap:
            break
    chosen.sort(key=lambda item: item[0])
    return "\n\n".join(p for _i, p in chosen)[:cap]


def ensure_entities_in_prompt(prompt: str, bible: dict, goal: str = "", criteria: str = "") -> str:
    """Append the look of selected cards missing from the generated SD prompt.

    The prompt builder rephrases the free-text request and can drop a named
    character/prop. The check is deterministic: a card whose id and aliases are
    all absent from the prompt gets its ``look`` appended, so the diffusion
    model still sees the entity.
    """
    text = str(prompt or "")
    if not text.strip():
        return text
    lowered = text.lower()
    additions = []
    for card in select_cards(bible, goal, criteria):
        card_id = str(card.get("id") or "").strip()
        aliases = [str(a) for a in (card.get("aliases") or []) if str(a).strip()]
        candidates = ([card_id] if card_id else []) + aliases
        if any(candidate.lower() in lowered for candidate in candidates if candidate):
            continue
        look = str(card.get("look") or "").strip()
        if look:
            additions.append(look)
    if not additions:
        return text
    return text.rstrip().rstrip(",") + ", " + ", ".join(additions)



