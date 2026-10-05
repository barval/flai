# Context Window

## Budget allocation

The table below shows how the effective context budget is distributed for the reasoning model at different context window sizes (values are approximate, measured in tokens). Budget formula: `available = ctx_len × 75% × 85% ≈ 64% of ctx_len`. Order of allocation: query → template (800 tokens) → search (RAG/web/history, ~30% of available) → SLM (up to 7 facts) → rolling summary → session history (the rest). Multimodal model uses its own simplified allocator: `available = ctx_len × 75%`, overhead 500 tokens, no search/SLM/summary — only session history.

| Context window (ctx_len) | Available budget (64%) | Template overhead | Search budget (30% avail) | SLM facts (≤7) | Remaining for history + summary |
|---|---|---|---|---|---|
| 8 192 (CPU default) | ~5 220 | 800 | ~1 566 | ~200 | ~2 650 (50%) |
| 16 384 (8–12 GB GPU) | ~10 445 | 800 | ~3 133 | ~200 | ~6 312 (60%) |
| 24 576 (16 GB GPU reasoning) | ~15 667 | 800 | ~4 700 | ~200 | ~9 967 (64%) |
| 32 768 (24 GB+ GPU multimodal) | ~20 889 | 800 | ~6 266 | ~200 | ~13 623 (64%) |

> **Why the history share grows with context size:** Template (800) and SLM (~200) are nearly fixed, so their % shrinks as the window grows. The search budget scales at ~30% of available, leaving a larger absolute remainder for conversation history on larger windows. On small windows (CPU 8192) history gets ~50%, on 32K it reaches ~64%.

---

See also [VRAM_MANAGEMENT.md](VRAM_MANAGEMENT.md) (how the window is auto-fitted at deployment) and [BENCHMARKS.md](BENCHMARKS.md) (measured throughput).
