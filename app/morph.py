# app/morph.py
"""Russian morphological analysis for room names using pymorphy3.

Generates key declension forms (nominative, accusative, prepositional)
for camera room names so the LLM router can recognize all grammatical
variants in user queries.
"""

import logging
from functools import lru_cache

import pymorphy3

logger = logging.getLogger(__name__)

# Key cases for camera room name recognition:
#   nomn (nominative) — "gostinaya", "kukhnya"            (base form)
#   accs (accusative) — "pokazhi gostinuyu", "snyay kukhnyu" (what to show/capture)
#   loct (locative)   — "chto v gostinoye", "na kukhne"     (where it is located)
_TARGET_CASES = ("nomn", "accs", "loct")

# Gender gramemes for adjective filtering
_GENDER_CASES = {"masc", "femn", "neut"}


@lru_cache(maxsize=1)
def _get_morph() -> pymorphy3.MorphAnalyzer:
    """Return singleton MorphAnalyzer instance."""
    return pymorphy3.MorphAnalyzer()


def _extract_gramemes(tag_str: str) -> set[str]:
    """Extract all gramemes from a pymorphy3 tag string.

    'NOUN,inan,femn sing,nomn' → {'NOUN', 'inan', 'femn', 'sing', 'nomn'}
    """
    gramemes: set[str] = set()
    for part in tag_str.split():
        for g in part.split(","):
            gramemes.add(g)
    return gramemes


def generate_room_name_forms(name: str) -> list[str]:
    """Generate key Russian declension forms for a room name.

    Returns a list of up to 3 forms: [nominative, accusative, prepositional].
    If the word is unknown to the dictionary, returns [name].
    Deduplicates forms (e.g. masculine nouns where nomn == accs).

    For adjectives (ADJF/ADJS), filters lexeme by the gender of the original
    word to avoid returning wrong-gender forms (e.g. "детская" → femn forms
    only, not masc "детский").

    Args:
        name: Room name in lowercase nominative case (e.g. "кухня").

    Returns:
        List of unique forms ordered as nomn → accs → loct.
    """
    if not name or not name.strip():
        return []

    name = name.strip().lower()
    morph = _get_morph()

    try:
        parsed = morph.parse(name)
    except Exception:
        logger.warning(f"pymorphy3 failed to parse '{name}', using raw name")
        return [name]

    if not parsed:
        return [name]

    # Use the most probable parse (first result)
    best = parsed[0]
    best_gramemes = _extract_gramemes(str(best.tag))

    # For adjectives, determine the gender to filter lexeme
    gender: str | None = None
    if best.tag.POS in ("ADJF", "ADJS"):
        for g in _GENDER_CASES:
            if g in best_gramemes:
                gender = g
                break

    # Build forms in canonical order: nomn -> accs -> loct.
    # For masculine inanimate nouns, nomn == accs (e.g. "tambour" both cases).
    # We skip the duplicate rather than picking a plural form.
    forms: list[str] = []
    nomn_word: str | None = None
    for case in _TARGET_CASES:
        for lexeme_form in best.lexeme:
            form_gramemes = _extract_gramemes(str(lexeme_form.tag))
            if gender and gender not in form_gramemes:
                continue
            if case in form_gramemes:
                word = lexeme_form.word
                if case == "nomn":
                    nomn_word = word
                    forms.append(word)
                elif case == "accs":
                    if word != nomn_word:
                        forms.append(word)
                    # else: nomn == accs, skip duplicate
                else:
                    forms.append(word)
                break

    return forms if forms else [name]
