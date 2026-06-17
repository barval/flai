# app/slm_merge.py
"""
Background fact merging for long-term memory.

Runs during idle time (5+ min) as 'fact_merge_task' on slow worker.
Conservative merging: only delete exact duplicates and clear contradictions.
Never merge different hobbies/aspects or facts that could be about different people.
"""

import logging
import time

logger = logging.getLogger(__name__)

# Idle threshold before merge runs (seconds)
MERGE_IDLE_THRESHOLD = 300  # 5 minutes


def should_run_merge(app) -> bool:
    """Check if enough time has passed since last task to run merge."""
    if not hasattr(app, "_last_task_time"):
        return False
    return (time.time() - app._last_task_time) > MERGE_IDLE_THRESHOLD


def merge_facts_for_user(llm_call, slm, user_id: str, lang: str = "ru") -> dict:
    """
    Merge facts for a user via LLM.

    Gets all facts, sends to LLM for merge/cleanup, updates SLM.

    Args:
        llm_call: The call_llamacpp function from base module.
        slm: The SLM module instance.
        user_id: User ID.
        lang: Language code.

    Returns:
        Stats dict with deleted/updated counts.
    """
    from app.utils import format_prompt

    stats = {"deleted": 0, "updated": 0, "skipped": 0}

    try:
        # Get all facts
        facts = slm.list_facts(limit=100, profile=user_id)
        if not facts or len(facts) < 3:
            return stats

        facts_str = "\n".join(
            f"[{f['id'][:8]}] {f['text']}" for f in facts
        )

        # LLM merge decision
        prompt = format_prompt(
            "slm_merge.template",
            {"facts": facts_str},
            lang=lang,
        )

        if not prompt:
            return stats

        result = llm_call(
            [{"role": "user", "content": prompt}],
            model_type="chat",
            lang=lang,
            temperature=0.1,
        )

        if not result or not isinstance(result, str):
            return stats

        # Parse JSON
        import json
        start = result.find("{")
        end = result.rfind("}") + 1
        if start == -1 or end <= start:
            return stats

        data = json.loads(result[start:end])
        deletions = data.get("delete", [])
        updates = data.get("update", [])

        # Apply deletions
        for item in deletions:
            fact_id = item.get("id", "")
            reason = item.get("reason", "")
            if fact_id:
                slm.delete_fact(fact_id, user_id)
                stats["deleted"] += 1
                logger.info(f"Deleted fact {fact_id}: {reason}")

        # Apply updates (delete old, create new with same metadata)
        for item in updates:
            old_id = item.get("id", "")
            new_text = item.get("new_text", "")
            if old_id and new_text:
                # Get old fact metadata
                old_fact = next((f for f in facts if f["id"] == old_id), None)
                if old_fact:
                    metadata = old_fact.get("metadata", {})
                    metadata["merged_from"] = old_id
                    slm.delete_fact(old_id, user_id)
                    slm.remember(
                        new_text,
                        metadata=metadata,
                        profile=user_id,
                    )
                    stats["updated"] += 1
                    logger.info(f"Updated fact {old_id}: {new_text[:50]}...")

        return stats

    except json.JSONDecodeError:
        logger.warning("Failed to parse merge response as JSON")
        return stats
    except Exception as e:
        logger.error(f"Fact merge failed: {e}")
        return stats
