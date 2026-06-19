# app/slm_extract.py
"""
Rule-based fact extraction for long-term memory.

Facts are extracted from Q&A exchanges via pattern matching (no LLM).
Explicit 'remember' requests use regex parsing with LLM fallback.

Runs as background thread (CPU-only, no GPU lock).
"""

import logging
import re

from app.slm_rules import extract_facts, extract_from_remember

logger = logging.getLogger(__name__)

# Queries that should NOT produce facts — user commands, not personal information
_SKIP_QUERY_PATTERNS = re.compile(
    r"^(сделай|напиши|нарисуй|покажи|сгенерируй|отредактируй|создай|запусти|видео| "
    r"make|write|draw|show|generate|edit|create|run|video| "
    r"который час|сколько время|what time|how many| "
    r"что ты умеешь|кто ты|what can you|who are you|"
    r"привет|здравствуй|hello|hi|hey)",
    re.IGNORECASE,
)


def extract_facts_from_exchange(
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
        max_facts: Maximum facts to extract.

    Returns:
        List of fact dicts with text, category, fact_type.
    """
    query_lower = query.strip().lower()
    if len(query_lower) < 5 or _SKIP_QUERY_PATTERNS.match(query_lower):
        logger.debug(f"Skipping fact extraction for command/greeting: {query[:50]}")
        return []

    if len(response.strip()) < 30:
        logger.debug("Skipping fact extraction: response too short")
        return []

    facts = extract_facts(query, response, existing_facts, lang=lang, max_facts=max_facts)
    if facts:
        logger.debug(f"Extracted {len(facts)} facts from exchange")
    return facts


def extract_facts_from_remember(
    query: str,
    lang: str = "ru",
    llm_call=None,
) -> list[str]:
    """
    Extract facts from explicit 'remember' request.

    Uses regex parsing first. Falls back to LLM if regex doesn't find
    a clear fact (e.g. complex conditional instructions).

    Args:
        query: User's request (e.g., "Помни, что мой день рождения 15 марта").
        lang: Language code.
        llm_call: Optional LLM function for complex edge cases. If None,
                  regex-only extraction is used.

    Returns:
        List of fact strings to save.
    """
    if not query or not query.strip():
        return []

    # Try regex extraction first
    facts = extract_from_remember(query, lang=lang)
    if facts:
        logger.debug(f"Extracted {len(facts)} facts from remember (regex)")
        return facts

    # Fallback to LLM for complex cases (if available)
    if llm_call:
        try:
            from app.utils import format_prompt

            prompt = format_prompt(
                "slm_remember.template",
                {"query": query},
                lang=lang,
            )
            if not prompt:
                return []

            import json

            result = llm_call(
                [{"role": "user", "content": prompt}],
                model_type="chat",
                lang=lang,
                temperature=0.1,
            )

            if not result or not isinstance(result, str):
                return []

            start = result.find("{")
            end = result.rfind("}") + 1
            if start == -1 or end <= start:
                return []

            data = json.loads(result[start:end])
            if data.get("confirmed"):
                return data.get("facts", [])
            return []

        except json.JSONDecodeError:
            logger.warning("Failed to parse remember LLM response as JSON")
            return []
        except Exception as e:
            logger.warning(f"LLM remember extraction failed: {e}")
            return []

    return []
