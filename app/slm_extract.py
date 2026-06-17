# app/slm_extract.py
"""
LLM-based fact extraction for long-term memory.

Facts are extracted from Q&A exchanges via chat model (not reasoning).
Runs as post-request on slow worker (serialized via _gpu_lock).
"""

import json
import logging

logger = logging.getLogger(__name__)


def extract_facts_from_exchange(
    llm_call,
    query: str,
    response: str,
    existing_facts: list[dict],
    lang: str = "ru",
    max_facts: int = 5,
) -> list[dict]:
    """
    Extract facts from Q&A via chat model.

    Args:
        llm_call: The call_llamacpp function from base module.
        query: User's question.
        response: Assistant's answer.
        existing_facts: Existing facts for context.
        lang: Language code.
        max_facts: Maximum facts to extract.

    Returns:
        List of fact dicts with text, category, fact_type.
    """
    from app.utils import format_prompt

    existing_str = "\n".join(f"- {f.get('text', '')}" for f in existing_facts if f.get("text")) if existing_facts else "(нет)"

    prompt = format_prompt(
        "slm_extract.template",
        {
            "existing_facts": existing_str,
            "query": query,
            "response": response,
        },
        lang=lang,
    )

    if not prompt:
        logger.warning("Failed to build slm_extract prompt")
        return []

    try:
        result = llm_call(
            [{"role": "user", "content": prompt}],
            model_type="chat",
            lang=lang,
            temperature=0.1,
        )

        if not result or not isinstance(result, str):
            return []

        # Parse JSON
        start = result.find("{")
        end = result.rfind("}") + 1
        if start == -1 or end <= start:
            return []

        data = json.loads(result[start:end])
        facts = data.get("facts", [])

        result_list = []
        for f in facts[:max_facts]:
            if isinstance(f, dict) and f.get("text"):
                result_list.append({
                    "text": f["text"],
                    "category": f.get("category", "context"),
                    "fact_type": f.get("fact_type", "general"),
                })
        return result_list

    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM response as JSON")
        return []
    except Exception as e:
        logger.error(f"Fact extraction failed: {e}")
        return []


def extract_facts_from_remember(
    llm_call,
    query: str,
    lang: str = "ru",
) -> list[str]:
    """
    Extract facts from explicit 'remember' request via chat model.

    Args:
        llm_call: The call_llamacpp function from base module.
        query: User's request (e.g., "Помни, что мой день рождения 15 марта").
        lang: Language code.

    Returns:
        List of fact strings to save.
    """
    from app.utils import format_prompt

    prompt = format_prompt(
        "slm_remember.template",
        {"query": query},
        lang=lang,
    )

    if not prompt:
        logger.warning("Failed to build slm_remember prompt")
        return []

    try:
        result = llm_call(
            [{"role": "user", "content": prompt}],
            model_type="chat",
            lang=lang,
            temperature=0.1,
        )

        if not result or not isinstance(result, str):
            return []

        # Parse JSON
        start = result.find("{")
        end = result.rfind("}") + 1
        if start == -1 or end <= start:
            return []

        data = json.loads(result[start:end])
        if data.get("confirmed"):
            return data.get("facts", [])
        return []

    except json.JSONDecodeError:
        logger.warning("Failed to parse remember response as JSON")
        return []
    except Exception as e:
        logger.error(f"Remember extraction failed: {e}")
        return []
