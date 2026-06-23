# app/slm_rules.py
"""
Rule-based fact extraction and merging for long-term memory (SLM).

Replaces LLM-based extraction with pattern matching, sentence scoring,
and semantic deduplication via the SLM daemon's embedding endpoint.

Pipeline:
  1. Regex pre-filter (skip commands, greetings)
  2. Sentence splitting + scoring by category patterns
  3. Deduplication vs existing facts (exact + semantic via /similarity)
  4. Category assignment from matched pattern

Runs as background thread (CPU-only, no GPU lock).
"""

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

# ── Query skip patterns (commands, greetings, meta-questions) ───────────

_SKIP_QUERY_PATTERNS = re.compile(
    r"^(сделай|напиши|нарисуй|покажи|сгенерируй|отредактируй|создай|запусти|видео| "
    r"make|write|draw|show|generate|edit|create|run|video|"
    r"который час|сколько время|what time|how many|"
    r"что ты умеешь|кто ты|what can you|who are you|"
    r"привет|здравствуй|hello|hi|hey)",
    re.IGNORECASE,
)

# ── Russian extraction patterns ─────────────────────────────────────────

_PREF_PATTERNS_RU = re.compile(
    r"(мне нравит|я любл|я предпочит|мой любим|я обожаю|я фанат|у меня слабость|"
    r"мой лучший|мой основной|я.choose|мой выбор)",
    re.IGNORECASE,
)

_FACT_PATTERNS_RU = re.compile(
    r"(я работа[юе]|я учусь|я живу|я из |мне \d+|мой возраст|"
    r"я женат|я замужем|у меня ест|мои? (хобби|увлечени)|"
    r"я занимаюсь|я профессионал|я специалист|я инженер|я программист|"
    r"я врач|я учитель|я дизайнер|я аналитик)",
    re.IGNORECASE,
)

_INSTR_PATTERNS_RU = re.compile(
    r"(всегда делай|никогда не|не используй|помни что|важно чтобы|"
    r"обязательно|запомни|используй только|предпочитаю|формат|"
    r"стиль ответ|отвечай|не отвечай|говори|не говори)",
    re.IGNORECASE,
)

_PERSON_PATTERNS_RU = re.compile(
    r"(зовут|имя|фамили|дочь|сын|жена|муж|родител|брат|сестра|"
    r"друг|коллег|ребёнок|дети|мама|папа|семья)",
    re.IGNORECASE,
)

# ── English extraction patterns ─────────────────────────────────────────

_PREF_PATTERNS_EN = re.compile(
    r"(i like|i love|i prefer|my favorite|i'm into|i'm a fan of|"
    r"my best|my main|my go-to|i choose|i picked)",
    re.IGNORECASE,
)

_FACT_PATTERNS_EN = re.compile(
    r"(i work|i live|i'm from|i'm \d+|my age|i'm married|i have|"
    r"my hobby|my hobbies|i do|my job|my role|i'm a|"
    r"i'm an|i work as|i study|i'm studying)",
    re.IGNORECASE,
)

_INSTR_PATTERNS_EN = re.compile(
    r"(always do|never use|don't use|remember that|it's important|"
    r"make sure|only use|prefer|format|style|answer|don't answer|"
    r"say|don't say)",
    re.IGNORECASE,
)

_PERSON_PATTERNS_EN = re.compile(
    r"(my name|called|daughter|son|wife|husband|parent|brother|sister|"
    r"friend|colleague|child|children|mom|dad|family)",
    re.IGNORECASE,
)

# ── Sentence splitting ──────────────────────────────────────────────────

_ABBREVIATIONS = re.compile(
    r"(?:т\. е\.|т\.к\.|т\. д\.|г\.|ул\.|п\.|с\.|др\.|"
    r"e\.g\.|i\.e\.|etc\.|vs\.|Mr\.|Mrs\.|Ms\.|Dr\.)",
    re.IGNORECASE,
)

_SENTENCE_SPLIT = re.compile(
    r"(?<=[.!?…])\s+(?=[А-ЯA-Z\u0410-\u042F])",
)


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, respecting abbreviations."""
    text = text.strip()
    if not text:
        return []

    # Protect abbreviations from splitting
    protected = {}
    for i, match in enumerate(_ABBREVIATIONS.finditer(text)):
        placeholder = f"__ABBREV{i}__"
        protected[placeholder] = match.group()
        text = text.replace(match.group(), placeholder, 1)

    # Split on sentence boundaries
    raw = _SENTENCE_SPLIT.split(text)

    # Restore abbreviations
    sentences = []
    for s in raw:
        for placeholder, original in protected.items():
            s = s.replace(placeholder, original)
        s = s.strip()
        if len(s) >= 15:
            sentences.append(s)

    return sentences


# ── Scoring ─────────────────────────────────────────────────────────────

def _score_sentence(sentence: str, lang: str) -> tuple[float, str]:
    """
    Score a sentence by pattern matches.

    Returns:
        (score, category) — score 0.0-1.0, category label.
    """
    score = 0.0
    category = "context"

    if lang == "ru":
        pref_patterns = _PREF_PATTERNS_RU
        fact_patterns = _FACT_PATTERNS_RU
        instr_patterns = _INSTR_PATTERNS_RU
        person_patterns = _PERSON_PATTERNS_RU
    else:
        pref_patterns = _PREF_PATTERNS_EN
        fact_patterns = _FACT_PATTERNS_EN
        instr_patterns = _INSTR_PATTERNS_EN
        person_patterns = _PERSON_PATTERNS_EN

    # Pattern matching — highest priority first
    if instr_patterns.search(sentence):
        score += 0.40
        category = "instruction"
    if pref_patterns.search(sentence):
        score += 0.40
        category = "preference" if category == "context" else category
    if fact_patterns.search(sentence):
        score += 0.40
        category = "fact" if category == "context" else category
    if person_patterns.search(sentence):
        score += 0.30
        category = "personality" if category == "context" else category

    # Length bonus — sweet spot 20-200 chars
    length = len(sentence)
    if 20 <= length <= 200:
        score += 0.10
    elif 15 <= length <= 300:
        score += 0.05

    # Not a question
    if not sentence.rstrip().endswith("?"):
        score += 0.05

    return min(score, 1.0), category


# ── Normalization helpers ───────────────────────────────────────────────

_PUNCT_TABLE = str.maketrans("", "", ".,!?;:\"'()-—…·")


def _normalize_text(text: str) -> str:
    """Normalize text for comparison: lowercase, strip punctuation/diacritics."""
    text = text.lower().strip()
    text = unicodedata.normalize("NFKD", text)
    text = text.translate(_PUNCT_TABLE)
    text = re.sub(r"\s+", " ", text)
    return text


def _levenshtein_ratio(s1: str, s2: str) -> float:
    """Compute normalized Levenshtein similarity (1.0 = identical)."""
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if not len1 or not len2:
        return 0.0

    # Optimization: if length difference > 50%, definitely different
    if abs(len1 - len2) / max(len1, len2) > 0.5:
        return 0.0

    # DP Levenshtein
    matrix = list(range(len2 + 1))
    for i in range(1, len1 + 1):
        prev, matrix[0] = matrix[0], i
        for j in range(1, len2 + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            prev, matrix[j] = matrix[j], min(
                matrix[j] + 1, matrix[j - 1] + 1, prev + cost
            )

    distance = matrix[len2]
    return 1.0 - distance / max(len1, len2)


# ── Model response filter ────────────────────────────────────────────────
# Detect typical assistant outputs that should NOT be stored as user facts.

_MODEL_RESPONSE_PATTERNS = re.compile(
    r"(^как я могу|^вот |^пожалуйста|^ваш ответ|^я подготовил|^я нашёл"
    r"|^я могу помочь|^готов помочь|^для вашего|^на основе|^вот ваш)"
    r"|(^how can i|^here is|^here's|^your answer|i can help|^ready to help|^based on)",
    re.IGNORECASE,
)

# News / fabricated content patterns — model-generated "facts" that are not about the user
_MODEL_CONTENT_PATTERNS = re.compile(
    r"(Последние новости|Запуск \w|EU AI Act|Соглашение|Крупный скандал"
    r"|Apple объявила|Государственная программа|Новые модели|Успешное испытание"
    r"|Санкция от|Новые стандарты|GPT-?\d|PaLM|Gemini|Siri|DeepMind)",
    re.IGNORECASE,
)


# ── Main extraction ─────────────────────────────────────────────────────

def extract_facts(
    query: str,
    response: str,
    existing_facts: list[dict],
    lang: str = "ru",
    max_facts: int = 5,
) -> list[dict]:
    """
    Extract facts from Q&A exchange using rule-based pattern matching.

    Args:
        query: User's question.
        response: Assistant's answer.
        existing_facts: Existing facts for deduplication.
        lang: Language code.
        max_facts: Maximum facts to return.

    Returns:
        List of fact dicts with text, category, fact_type.
    """
    query_lower = query.strip().lower()
    if len(query_lower) < 5 or _SKIP_QUERY_PATTERNS.match(query_lower):
        return []

    # Skip model's self-referential or news-hallucination responses
    resp_lower = response.strip().lower()
    if _MODEL_RESPONSE_PATTERNS.match(resp_lower[:60]):
        return []
    if _MODEL_CONTENT_PATTERNS.search(resp_lower[:200]):
        return []

    # Extract facts from the USER's query, not the model's response
    # User statements about themselves ("я работаю программистом") contain real facts.
    sentences = _split_sentences(query)
    if not sentences:
        return []

    # Score all sentences
    scored: list[tuple[float, str, str]] = []
    for s in sentences:
        score, category = _score_sentence(s, lang)
        if score >= 0.50:
            scored.append((score, s, category))

    if not scored:
        return []

    # Sort by score descending, take top N
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:max_facts]

    # Build existing normalized set for fast dedup
    existing_norms: set[str] = set()
    for f in existing_facts:
        text = (f.get("text") or f.get("content") or "").strip()
        if text:
            existing_norms.add(_normalize_text(text))

    # Deduplicate results
    results: list[dict] = []
    seen_norms: set[str] = set()
    for _, sentence, category in top:
        text = sentence.strip()
        if len(text) > 200:
            text = text[:197] + "..."

        norm = _normalize_text(text)
        if norm in existing_norms or norm in seen_norms:
            continue

        # Check substring overlap with existing facts
        is_fragment = False
        for existing_norm in existing_norms:
            if norm in existing_norm and len(norm) < len(existing_norm) * 0.7:
                is_fragment = True
                break
        if is_fragment:
            continue

        seen_norms.add(norm)
        results.append({
            "text": text,
            "category": category,
            "fact_type": "general",
        })

    return results


def extract_from_remember(query: str, lang: str = "ru") -> list[str]:
    """
    Extract facts from explicit 'remember' request using regex.

    Handles patterns like:
      - "Помни, что X"
      - "Запомни: X"
      - "Remember that X"
      - "Remember: X"

    Falls back to the full text if no prefix matched.

    Args:
        query: User's remember request.
        lang: Language code.

    Returns:
        List of fact strings.
    """
    if not query or not query.strip():
        return []

    text = query.strip()

    # Russian prefixes
    ru_prefixes = [
        r"^помни[,\s]+что\s+",
        r"^запомни[,:]\s*",
        r"^запомни[,\s]+что\s+",
    ]
    for prefix in ru_prefixes:
        match = re.match(prefix, text, re.IGNORECASE)
        if match:
            fact = text[match.end():].strip()
            if fact and len(fact) >= 5:
                return [fact[:200]]

    # English prefixes
    en_prefixes = [
        r"^remember\s+that\s+",
        r"^remember[,:]\s*",
    ]
    for prefix in en_prefixes:
        match = re.match(prefix, text, re.IGNORECASE)
        if match:
            fact = text[match.end():].strip()
            if fact and len(fact) >= 5:
                return [fact[:200]]

    # Fallback: use the full text as one fact
    if len(text) >= 5:
        return [text[:200]]

    return []
