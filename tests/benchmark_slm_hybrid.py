#!/usr/bin/env python3
"""
Benchmark: compare SLM recall latency and quality — old (semantic) vs new (hybrid).

Usage (from project root, after building flai-slm image):
  .venv/bin/python tests/benchmark_slm_hybrid.py [--profile PROFILE] [--rounds N]

Requires flai-slm container running (docker compose --profile with-slm up).
"""

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SLM_URL = "http://localhost:8766"

# ── Test queries: (query, expected_fact_snippet) ──────────────────────

TEST_QUERIES_RU = [
    # Keyword-hittable (should be fast via keyword path)
    ("Как зовут мою собаку?", "Рекс"),
    ("Где я работаю?", "программистом"),
    ("Какая у меня кошка?", "Мурка"),
    ("Какой мой любимый цвет?", "синий"),
    ("Что я делал вчера вчера?", "кино"),
    # Temporal
    ("Что я делал вчера?", "кино"),
    # Semantic-only (paraphrase, no exact keyword overlap)
    ("Какое занятие у меня на работе?", "программистом"),
    ("Какое домашнее животное у меня есть?", "собака"),
    # Unanswerable (should return empty or unrelated)
    ("Какая погода на Марсе?", None),
]

TEST_QUERIES_EN = [
    ("What is my dog's name?", "Рекс"),
    ("Where do I work?", "программистом"),
    ("What did I do yesterday?", "кино"),
    ("What color do I like?", "синий"),
]


def _recall(query: str, profile: str, semantic: bool) -> tuple[list[dict], float]:
    """Call SLM /recall and return (results, elapsed_ms)."""
    body = json.dumps(
        {
            "query": query,
            "limit": 5,
            "profile": profile,
            "semantic": semantic,
        }
    ).encode()
    req = urllib.request.Request(
        f"{SLM_URL}/recall",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read().decode())
        elapsed = (time.perf_counter() - t0) * 1000
        return data.get("data", {}).get("results", []), elapsed
    except Exception as e:
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"  ERROR: {e}")
        return [], elapsed


def _check_health() -> bool:
    try:
        with urllib.request.urlopen(f"{SLM_URL}/health", timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _seed_facts(profile: str) -> None:
    """Insert test facts via /remember if user has no data."""
    facts = [
        "Моя собака Рекс — лабрадор",
        "Я работаю программистом в Google",
        "Вчера я ходил в кино на фильм Дюна",
        "Мой любимый цвет — синий",
        "У меня есть кошка Мурка",
    ]
    for text in facts:
        body = json.dumps({"text": text, "profile": profile}).encode()
        req = urllib.request.Request(
            f"{SLM_URL}/remember",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=30)
        except Exception as e:
            print(f"  WARN: remember failed for '{text[:30]}...': {e}")


def run_benchmark(profile: str, rounds: int, seed: bool) -> None:
    print(f"SLM URL:       {SLM_URL}")
    print(f"Profile:       {profile}")
    print(f"Rounds:        {rounds}")
    print(f"Seed facts:    {'yes' if seed else 'no'}")
    print()

    if not _check_health():
        print("ERROR: flai-slm is not reachable. Start it first:")
        print("  docker compose -f docker-compose.gpu.yml --profile with-slm up -d")
        sys.exit(1)
    print("SLM health: OK")

    if seed:
        print("Seeding test facts...")
        _seed_facts(profile)
        time.sleep(1)
        print("Seed complete.\n")

    queries = TEST_QUERIES_RU + TEST_QUERIES_EN

    # Collect results: (query, expected, old_times, old_results, new_times, new_results)
    results = []

    for query, expected in queries:
        old_times, old_results = [], []
        new_times, new_results = [], []

        for _ in range(rounds):
            # Old path: semantic=True (full daemon)
            r, t = _recall(query, profile, semantic=True)
            old_times.append(t)
            old_results = r

            # New path: semantic=False (hybrid keyword → semantic → latest)
            r, t = _recall(query, profile, semantic=False)
            new_times.append(t)
            new_results = r

        old_median = statistics.median(old_times)
        new_median = statistics.median(new_times)
        speedup = old_median / new_median if new_median > 0 else float("inf")

        old_hit = any(expected.lower() in (r.get("content", "").lower()) for r in old_results) if expected else None
        new_hit = any(expected.lower() in (r.get("content", "").lower()) for r in new_results) if expected else None

        results.append(
            {
                "query": query,
                "expected": expected,
                "old_ms": old_median,
                "new_ms": new_median,
                "speedup": speedup,
                "old_hit": old_hit,
                "new_hit": new_hit,
                "old_count": len(old_results),
                "new_count": len(new_results),
            }
        )

    # ── Print table ────────────────────────────────────────────────────
    print(f"{'Query':<40} {'Old ms':>8} {'New ms':>8} {'Speed':>6} {'Old hit':>8} {'New hit':>8}")
    print("-" * 88)
    for r in results:
        q = r["query"][:38]
        old_hit = "✓" if r["old_hit"] else ("✗" if r["old_hit"] is False else "—")
        new_hit = "✓" if r["new_hit"] else ("✗" if r["new_hit"] is False else "—")
        speedup = f"{r['speedup']:.1f}x"
        print(f"{q:<40} {r['old_ms']:>7.0f} {r['new_ms']:>7.0f} {speedup:>6} {old_hit:>8} {new_hit:>8}")

    # ── Summary ────────────────────────────────────────────────────────
    timed_results = [r for r in results if r["old_ms"] > 0]
    if timed_results:
        avg_speedup = statistics.mean(r["speedup"] for r in timed_results)
        fast_count = sum(1 for r in timed_results if r["new_ms"] < r["old_ms"])
        print()
        print(f"Average speedup:       {avg_speedup:.1f}x")
        print(f"Queries faster (new):  {fast_count}/{len(timed_results)}")
        print(f"Latency saved/query:   ~{statistics.mean(r['old_ms'] - r['new_ms'] for r in timed_results):.0f} ms")

    # ── Quality comparison ─────────────────────────────────────────────
    answerable = [r for r in results if r["expected"]]
    if answerable:
        old_quality = sum(1 for r in answerable if r["old_hit"])
        new_quality = sum(1 for r in answerable if r["new_hit"])
        print()
        print(f"Answerable queries:    {len(answerable)}")
        print(f"  Old semantic hit:    {old_quality}/{len(answerable)}")
        print(f"  New hybrid hit:      {new_quality}/{len(answerable)}")
        if new_quality > old_quality:
            print(f"  → Hybrid IMPROVED quality by +{new_quality - old_quality}")
        elif new_quality < old_quality:
            print(f"  → Hybrid dropped quality by {new_quality - old_quality} — investigate keyword threshold")
        else:
            print("  → Quality maintained")

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark SLM recall: semantic vs hybrid")
    parser.add_argument("--profile", default="benchmark_user", help="SLM profile to use")
    parser.add_argument("--rounds", type=int, default=3, help="Number of measurement rounds per query")
    parser.add_argument("--no-seed", action="store_true", help="Skip seeding test facts")
    args = parser.parse_args()
    run_benchmark(args.profile, args.rounds, seed=not args.no_seed)
