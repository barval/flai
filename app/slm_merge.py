# app/slm_merge.py
"""
Background fact merging for long-term memory — rule-based (no LLM).

Pipeline:
  1. fast_cleanup — exact duplicates, fragments, short garbage
  2. edit_distance_merge — normalized Levenshtein for near-duplicates
  3. semantic_merge — HTTP /similarity for fuzzy deduplication
  4. fragment_merge — stricter substring detection (70% threshold)
  5. temporal_decay — auto-archive facts older than N days with low confidence

Runs during idle time (5+ min) as 'fact_merge_task' in background queue.
All operations are CPU-only, no GPU lock.
"""

import logging
import re
from datetime import UTC, datetime, timedelta

from app.slm_rules import _levenshtein_ratio, _normalize_text

logger = logging.getLogger(__name__)

_DEFAULT_SIMILARITY_THRESHOLD = 0.85
_DEFAULT_TEMPORAL_DECAY_DAYS = 90
_DEFAULT_MIN_CONFIDENCE_FOR_DECAY = 0.5
_DEFAULT_LEVENSHTEIN_THRESHOLD = 0.25
_DEFAULT_FRAGMENT_RATIO = 0.7


def fast_cleanup(facts: list[dict]) -> tuple[list[str], list[dict]]:
    """
    Deterministic cleanup without LLM.

    Removes:
      - Facts shorter than 5 characters (garbage)
      - Exact duplicates (case-insensitive)
      - Fragments that are substrings of longer facts (<80% length)

    Args:
        facts: List of fact dicts with 'fact_id'/'id' and 'content'/'text'.

    Returns:
        (ids_to_delete, remaining_facts) — remaining sorted newest-first.
    """
    seen: dict[str, str] = {}  # normalized_text → original content
    to_delete: list[str] = []
    remaining: list[dict] = []

    sorted_facts = sorted(facts, key=lambda f: f.get("created_at", 0), reverse=True)

    for f in sorted_facts:
        fid = f.get("fact_id") or f.get("id", "")
        content = (f.get("content") or f.get("text") or "").strip()

        if len(content) < 5:
            to_delete.append(fid)
            continue

        normalized = content.lower()

        if normalized in seen:
            to_delete.append(fid)
            continue

        is_fragment = False
        for existing_content in seen.values():
            if normalized in existing_content.lower() and len(normalized) < len(existing_content) * 0.8:
                to_delete.append(fid)
                is_fragment = True
                break

        if not is_fragment:
            seen[normalized] = content
            remaining.append(f)

    return to_delete, remaining


def edit_distance_merge(facts: list[dict], threshold: float = _DEFAULT_LEVENSHTEIN_THRESHOLD) -> list[str]:
    """
    Remove near-duplicates using normalized Levenshtein distance.

    Two facts are duplicates if their normalized similarity > (1 - threshold).
    Keeps the newer fact (first in list, sorted by created_at DESC).

    Args:
        facts: List of fact dicts sorted newest-first.
        threshold: Max normalized edit distance to consider as duplicate (0.25 = 75% similar).

    Returns:
        List of fact IDs to delete.
    """
    if len(facts) < 2:
        return []

    to_delete: list[str] = []
    kept: list[str] = []  # normalized texts of kept facts

    for f in facts:
        fid = f.get("fact_id") or f.get("id", "")
        content = (f.get("content") or f.get("text") or "").strip()
        norm = _normalize_text(content)

        if not norm:
            to_delete.append(fid)
            continue

        is_dup = False
        for kept_norm in kept:
            if _levenshtein_ratio(norm, kept_norm) > (1.0 - threshold):
                is_dup = True
                break

        if is_dup:
            to_delete.append(fid)
        else:
            kept.append(norm)

    return to_delete


def fragment_merge(facts: list[dict], ratio: float = _DEFAULT_FRAGMENT_RATIO) -> list[str]:
    """
    Remove facts that are substrings of longer facts (stricter than fast_cleanup).

    A fact A is a fragment of B if A ⊂ B and len(A) < ratio * len(B).

    Args:
        facts: List of fact dicts sorted newest-first.
        ratio: Max length ratio to consider as fragment (0.7 = A must be <70% of B).

    Returns:
        List of fact IDs to delete.
    """
    if len(facts) < 2:
        return []

    to_delete: list[str] = []
    kept: list[tuple[str, str]] = []  # (normalized, original content)

    for f in facts:
        fid = f.get("fact_id") or f.get("id", "")
        content = (f.get("content") or f.get("text") or "").strip()
        norm = _normalize_text(content)

        if not norm:
            to_delete.append(fid)
            continue

        is_fragment = False
        for kept_norm, _ in kept:
            if norm in kept_norm and len(norm) < len(kept_norm) * ratio:
                is_fragment = True
                break
            if kept_norm in norm and len(kept_norm) < len(norm) * ratio:
                # The existing kept fact is a fragment of this new one
                # Mark the old one for deletion instead
                break

        if is_fragment:
            to_delete.append(fid)
        else:
            kept.append((norm, content))

    return to_delete


def semantic_merge(
    facts: list[dict],
    similarity_threshold: float = _DEFAULT_SIMILARITY_THRESHOLD,
) -> list[str]:
    """
    Remove semantically similar facts using the SLM daemon's embedding model.

    For each fact, queries the /similarity endpoint. If the closest existing
    fact has similarity above threshold, the fact is considered a duplicate.

    Note: This runs HTTP calls sequentially. For 100 facts this takes ~5-10s
    but runs on CPU without blocking GPU.

    Args:
        facts: List of fact dicts sorted newest-first.
        similarity_threshold: Minimum similarity to consider as duplicate (0.85).

    Returns:
        List of fact IDs to delete.
    """
    if len(facts) < 2:
        return []

    # Lazy import to avoid circular dependency at module level
    from flask import current_app

    slm = None
    try:
        if current_app:
            slm = current_app.modules.get("slm")  # type: ignore[attr-defined]
    except RuntimeError:
        pass

    if not slm or not slm.available:
        return []

    to_delete: list[str] = []
    kept_texts: list[str] = []

    for f in facts:
        fid = f.get("fact_id") or f.get("id", "")
        content = (f.get("content") or f.get("text") or "").strip()

        if not content:
            to_delete.append(fid)
            continue

        try:
            similarity = slm.check_similarity(content)
        except Exception:
            # On error, keep the fact (don't delete on failed check)
            kept_texts.append(content)
            continue

        # If similarity to ANY already-kept fact is above threshold → duplicate
        # The /similarity endpoint checks against ALL facts in DB, not just kept_texts
        # So we rely on the daemon's check. If similarity > threshold → delete
        if similarity >= similarity_threshold:
            to_delete.append(fid)
        else:
            kept_texts.append(content)

    return to_delete


def temporal_decay(
    facts: list[dict],
    decay_days: int = _DEFAULT_TEMPORAL_DECAY_DAYS,
    min_confidence: float = _DEFAULT_MIN_CONFIDENCE_FOR_DECAY,
) -> list[str]:
    """
    Auto-archive old facts with low confidence.

    Facts older than decay_days with confidence < min_confidence are archived.

    Args:
        facts: List of fact dicts.
        decay_days: Number of days after which old facts are candidates.
        min_confidence: Minimum confidence to survive decay.

    Returns:
        List of fact IDs to archive (delete).
    """
    cutoff = datetime.now(UTC) - timedelta(days=decay_days)
    to_delete: list[str] = []

    for f in facts:
        fid = f.get("fact_id") or f.get("id", "")
        created_at = f.get("created_at", "")
        score = f.get("score", 1.0)  # default high confidence

        if not created_at:
            continue

        try:
            # Handle various timestamp formats
            if isinstance(created_at, (int, float)):
                fact_date = datetime.fromtimestamp(created_at, tz=UTC)
            else:
                # Try common formats
                for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        fact_date = datetime.strptime(str(created_at)[:10], fmt).replace(tzinfo=UTC)
                        break
                    except ValueError:
                        continue
                else:
                    continue

            if fact_date < cutoff and score < min_confidence:
                to_delete.append(fid)
                logger.debug(f"Temporal decay: archiving {fid} (age={fact_date.date()}, score={score})")

        except Exception:
            continue

    return to_delete


_MODEL_RESPONSE_PATTERNS = re.compile(
    r"(?:^|\s)(?:как я могу|вот |пожалуйста|ваш ответ|я подготовил|я нашёл"
    r"|я могу помочь|готов помочь|для вашего|на основе|вот ваш"
    r"|how can i|here is|here.s|your answer|i can help|ready to help|based on)",
    re.IGNORECASE,
)

_MODEL_CONTENT_PATTERNS = re.compile(
    r"(?:Последние новости|Запуск \w|EU AI Act|Соглашение|Крупный скандал"
    r"|Apple объявила|Государственная программа|Новые модели|Успешное испытание"
    r"|Санкция от|Новые стандарты|GPT-?\d|PaLM|Gemini|Siri|DeepMind)",
    re.IGNORECASE,
)


def _is_model_response(fact_text: str) -> bool:
    """Check if a fact is a model response, not a user fact."""
    if not fact_text:
        return False
    return bool(
        _MODEL_RESPONSE_PATTERNS.search(fact_text[:60])
        or _MODEL_CONTENT_PATTERNS.search(fact_text[:200])
    )


def merge_facts_for_user(slm, user_id: str, lang: str = "ru") -> dict:
    """
    Merge facts for a user: rule-based pipeline (no LLM).

    Pipeline:
      0. model_response_cleanup — remove model responses that are not user facts
      1. fast_cleanup — exact duplicates, fragments, short garbage
      2. edit_distance_merge — Levenshtein near-duplicates
      3. fragment_merge — stricter substring detection
      4. semantic_merge — embedding similarity (via SLM daemon)
      5. temporal_decay — auto-archive old low-confidence facts

    Args:
        slm: SLM module instance.
        user_id: User ID.
        lang: Language code (unused in rule-based, kept for API compat).

    Returns:
        Stats dict with deleted/fast_deleted/semantic_deleted/decay_deleted counts.
    """
    stats = {
        "deleted": 0,
        "fast_deleted": 0,
        "edit_deleted": 0,
        "fragment_deleted": 0,
        "semantic_deleted": 0,
        "decay_deleted": 0,
        "skipped": 0,
    }

    try:
        from flask import current_app

        facts = slm.list_facts(limit=current_app.config.get("MERGE_MAX_FACTS", 100), profile=user_id)
        logger.info(f"Merge for {user_id}: got {len(facts) if facts else 0} facts")
        if not facts or len(facts) < 3:
            return stats

        # Step 0: Remove model responses that are not user facts
        model_junk = [f for f in facts if _is_model_response(f.get("content", ""))]
        for f in model_junk:
            fid = f.get("fact_id") or f.get("id")
            if fid:
                slm.archive_fact(fid, user_id) if hasattr(slm, "archive_fact") else slm.delete_fact(fid, user_id)
                stats["fast_deleted"] += 1
        remaining = [f for f in facts if f not in model_junk]
        if model_junk:
            logger.info(f"Merge for {user_id}: model_response_cleanup removed {len(model_junk)} facts")
        facts = remaining

        if len(facts) < 3:
            return stats

        # Step 1: Fast cleanup (exact duplicates, fragments, garbage)
        fast_deleted, remaining = fast_cleanup(facts)
        for fid in fast_deleted:
            slm.delete_fact(fid, user_id)
            stats["fast_deleted"] += 1
        logger.info(f"Merge for {user_id}: fast_cleanup removed {len(fast_deleted)} facts")

        if len(remaining) < 3:
            return stats

        # Step 2: Edit distance merge (Levenshtein near-duplicates)
        edit_dups = edit_distance_merge(remaining)
        for fid in edit_dups:
            slm.delete_fact(fid, user_id)
            stats["edit_deleted"] += 1
        if edit_dups:
            logger.info(f"Merge for {user_id}: edit_distance removed {len(edit_dups)} facts")

        # Step 3: Fragment merge (stricter substring detection)
        frag_dups = fragment_merge(remaining)
        for fid in frag_dups:
            slm.delete_fact(fid, user_id)
            stats["fragment_deleted"] += 1
        if frag_dups:
            logger.info(f"Merge for {user_id}: fragment_merge removed {len(frag_dups)} facts")

        # Step 4: Semantic merge (embedding similarity via daemon)
        try:
            from flask import current_app
            sim_threshold = current_app.config.get("SLM_SIMILARITY_THRESHOLD", _DEFAULT_SIMILARITY_THRESHOLD)
        except (ImportError, KeyError):
            sim_threshold = _DEFAULT_SIMILARITY_THRESHOLD

        # Reload facts after local deletions to get accurate list
        merge_limit = current_app.config.get("MERGE_MAX_FACTS", 100)
        facts_after = slm.list_facts(limit=merge_limit, profile=user_id)
        if facts_after and len(facts_after) >= 3:
            sem_dups = semantic_merge(facts_after, similarity_threshold=sim_threshold)
            for fid in sem_dups:
                slm.delete_fact(fid, user_id)
                stats["semantic_deleted"] += 1
            if sem_dups:
                logger.info(f"Merge for {user_id}: semantic_merge removed {len(sem_dups)} facts")

        # Step 5: Temporal decay (old low-confidence facts)
        try:
            from flask import current_app
            decay_days = current_app.config.get("SLM_TEMPORAL_DECAY_DAYS", _DEFAULT_TEMPORAL_DECAY_DAYS)
            min_conf = current_app.config.get("SLM_MIN_CONFIDENCE_FOR_DECAY", _DEFAULT_MIN_CONFIDENCE_FOR_DECAY)
        except (ImportError, KeyError):
            decay_days = _DEFAULT_TEMPORAL_DECAY_DAYS
            min_conf = _DEFAULT_MIN_CONFIDENCE_FOR_DECAY

        facts_final = slm.list_facts(limit=merge_limit, profile=user_id)
        if facts_final:
            decay_dups = temporal_decay(facts_final, decay_days=decay_days, min_confidence=min_conf)
            for fid in decay_dups:
                slm.delete_fact(fid, user_id)
                stats["decay_deleted"] += 1
            if decay_dups:
                logger.info(f"Merge for {user_id}: temporal_decay removed {len(decay_dups)} facts")

        stats["deleted"] = (
            stats["fast_deleted"] + stats["edit_deleted"] + stats["fragment_deleted"]
            + stats["semantic_deleted"] + stats["decay_deleted"]
        )
        logger.info(f"Merge for {user_id}: total removed {stats['deleted']} facts")
        return stats

    except Exception as e:
        logger.error(f"Fact merge failed: {e}")
        return stats
