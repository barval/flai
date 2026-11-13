"""Typed personal facts with validity intervals.

A free-text fact turns every contradiction into a judgement call: "I moved to
Tbilisi" and "I live in Moscow" are different sentences, and deciding that the
second one no longer holds needs a model call per pair. Measured on a synthetic
profile, four runs of the same consolidation produced four different splits —
one of them inverted a preference outright.

A triple makes the same question a lookup: same subject, same predicate, a
different value. Closing the old row is then a rule, not an opinion, and the
interval it carries is what answers "where did I live in 2024".

The predicate list is closed on purpose. An open vocabulary turns a typo into a
second predicate, and two predicates that mean the same thing never supersede
each other — which is the bug this module exists to prevent.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

#: Canonical predicate -> what it means. Kept in English: this is a schema, not
#: prose, and the extractor prompt explains each one in the user's language.
PREDICATES: dict[str, str] = {
    "lives_in": "the place the user lives",
    "works_at": "the employer",
    "works_as": "the job title or role",
    "studied_at": "a school or university",
    "born_in": "the place of birth",
    "born_on": "the date of birth",
    "has_pet": "a pet",
    "likes": "something the user likes",
    "dislikes": "something the user avoids",
    "speaks": "a language the user speaks",
    "owns": "a durable thing the user owns",
    "lives_with": "who the user lives with",
    "married_to": "the spouse",
}

MIN_CONFIDENCE = 0.6
MAX_VALUE_CHARS = 120
MAX_SENTENCE_CHARS = 200
_SUBJECT = "user"
_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[.,;:!?\"'()\[\]]+$")


def normalize_value(value: str) -> str:
    """Comparison key for a value: case, spacing and trailing punctuation do not matter."""
    cleaned = _WHITESPACE.sub(" ", (value or "").strip().lower())
    return _PUNCTUATION.sub("", cleaned)


def _valid_from(raw: Any) -> datetime:
    """A stated start date, or now when the fact carries none."""
    text = str(raw or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return datetime.now(UTC)


def parse_triples(answer: str) -> list[dict[str, Any]]:
    """Validate a model answer into storable triples.

    Strict on purpose: an unknown predicate is dropped rather than stored, since
    a fact that can never be superseded is worse than a missing fact.
    """
    body = (answer or "").strip()
    if not body:
        return []
    payload: Any
    try:
        payload = json.loads(body)
    except ValueError:
        start, end = body.find("["), body.rfind("]")
        if start == -1 or end <= start:
            return []
        try:
            payload = json.loads(body[start : end + 1])
        except ValueError:
            return []
    if isinstance(payload, dict):
        payload = payload.get("facts", [])
    if not isinstance(payload, list):
        return []

    facts: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        predicate = str(item.get("predicate") or "").strip().lower()
        if predicate not in PREDICATES:
            continue
        value = _WHITESPACE.sub(" ", str(item.get("value") or "").strip())
        if not value or len(value) > MAX_VALUE_CHARS:
            continue
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        if confidence < MIN_CONFIDENCE:
            continue
        sentence = _WHITESPACE.sub(" ", str(item.get("sentence") or "").strip())[:MAX_SENTENCE_CHARS]
        facts.append(
            {
                "subject": str(item.get("subject") or _SUBJECT).strip().lower() or _SUBJECT,
                "predicate": predicate,
                "value": value,
                "value_norm": normalize_value(value),
                "sentence": sentence or value,
                "kind": str(item.get("kind") or "profile").strip().lower(),
                "confidence": round(min(confidence, 1.0), 2),
                "valid_from": _valid_from(item.get("valid_from")),
                "source": str(item.get("source") or "llm_extract").strip()[:40],
            }
        )
    return facts


def store_triple(conn, user_id: str, fact: dict[str, Any]) -> str:
    """Store one triple, closing whatever it contradicts.

    Returns ``added``, ``superseded`` (an active fact was closed) or
    ``unchanged`` (the same value is already the current one).
    """
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT id, value_norm FROM memory_facts "
            "WHERE user_id = %s AND subject = %s AND predicate = %s AND valid_until IS NULL",
            (user_id, fact["subject"], fact["predicate"]),
        )
        active = cursor.fetchall()
        if any(row["value_norm"] == fact["value_norm"] for row in active):
            cursor.execute(
                "UPDATE memory_facts SET confidence = GREATEST(confidence, %s) "
                "WHERE user_id = %s AND subject = %s AND predicate = %s AND value_norm = %s",
                (fact["confidence"], user_id, fact["subject"], fact["predicate"], fact["value_norm"]),
            )
            return "unchanged"

        cursor.execute(
            """
            INSERT INTO memory_facts
                (user_id, subject, predicate, value, value_norm, sentence, kind, confidence,
                 source, valid_from)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, subject, predicate, value_norm) DO UPDATE SET
                valid_until = NULL,
                superseded_by = NULL,
                sentence = EXCLUDED.sentence,
                confidence = GREATEST(memory_facts.confidence, EXCLUDED.confidence),
                source = EXCLUDED.source
            RETURNING id
            """,
            (
                user_id,
                fact["subject"],
                fact["predicate"],
                fact["value"],
                fact["value_norm"],
                fact["sentence"],
                fact["kind"],
                fact["confidence"],
                fact["source"],
                fact["valid_from"],
            ),
        )
        new_id = cursor.fetchone()["id"]

        if active:
            # The old value stops being true when the new one starts, not when we
            # happened to learn about it.
            cursor.execute(
                "UPDATE memory_facts SET valid_until = %s, superseded_by = %s WHERE id = ANY(%s::uuid[])",
                (fact["valid_from"], new_id, [str(row["id"]) for row in active]),
            )
            return "superseded"
        return "added"


def active_triples(conn, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """What holds now, newest first."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT predicate, value, sentence, confidence, valid_from FROM memory_facts "
            "WHERE user_id = %s AND valid_until IS NULL ORDER BY valid_from DESC LIMIT %s",
            (user_id, limit),
        )
        return list(cursor.fetchall())


def closed_triples(conn, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """What used to hold, with the interval it held for."""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT predicate, value, sentence, valid_from, valid_until FROM memory_facts "
            "WHERE user_id = %s AND valid_until IS NOT NULL ORDER BY valid_until DESC LIMIT %s",
            (user_id, limit),
        )
        return list(cursor.fetchall())
