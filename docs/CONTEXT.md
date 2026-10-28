# Context Window

## Budget allocation

The table below shows how the effective context budget is distributed for the reasoning model at different context window sizes (values are approximate, measured in tokens). Budget formula: `available = ctx_len × 75% × 85% ≈ 64% of ctx_len`. Order of allocation: query → template (800 tokens) → search (RAG/web/history, ~30% of available) → SLM (up to 7 facts) → rolling summary → session history (the rest). The number of retrieved document chunks is bounded by retrieval itself (`top_k` plus the coverage pass) and then by this budget — there is no separate chunk count cap, because a fixed cap silently dropped the tail of a document's own chunks. Multimodal model uses its own simplified allocator: `available = ctx_len × 75%`, overhead 500 tokens, no search/SLM/summary — only session history.

| Context window (ctx_len) | Available budget (64%) | Template overhead | Search budget (30% avail) | SLM facts (≤7) | Remaining for history + summary |
|---|---|---|---|---|---|
| 8 192 (CPU default) | ~5 220 | 800 | ~1 566 | ~200 | ~2 650 (50%) |
| 16 384 (8–12 GB GPU) | ~10 445 | 800 | ~3 133 | ~200 | ~6 312 (60%) |
| 24 576 (16 GB GPU reasoning) | ~15 667 | 800 | ~4 700 | ~200 | ~9 967 (64%) |
| 32 768 (24 GB+ GPU multimodal) | ~20 889 | 800 | ~6 266 | ~200 | ~13 623 (64%) |

## Assembly order

Inside the system prompt the sections are ordered **search → summary → history → long-term facts**.
The recalled fact set changes with every query while the transcript only grows, so putting the facts
last keeps the llama.cpp prefix cache intact: anything after them is re-processed on every turn (the
transcript alone is ~5k tokens, ~1.5 s of prefill on a 16 GB GPU).

The reasoning prompt is two messages: a `system` turn with the rules, role, **current date and time**
(`{current_time_str}`) and the skills list, and one `user` turn where the evidence comes first and the
question is the last thing the model reads. It used to be a single `user` message with the question
above the transcript, so the model read the question first and the evidence it had to ground sat
mid-prompt. History deliberately carries no timestamps — the system turn is the only time anchor.

The rolling summary is **not** regenerated in the request. When history is trimmed, the request reads
the stored summary and queues a refresh (`session_summary_task`, idle work, one per session); the old
path loaded the multimodal model inside the reasoning request, summarised, and then loaded the
reasoning model — a second model swap on the critical path.

The thinking budget is derived from the same fill ratio rather than from the window alone:
`reasoning_budget_tokens(ctx) = max(1024, min(ctx × 0.4, ctx − 64% × ctx − 1500))`. A flat
`ctx × 0.4` (9 830 on 24 576) could only be spent by eating the answer, because the prompt may
already occupy 64% of the window.

> **Why the history share grows with context size:** Template (800) and SLM (~200) are nearly fixed, so their % shrinks as the window grows. The search budget scales at ~30% of available, leaving a larger absolute remainder for conversation history on larger windows. On small windows (CPU 8192) history gets ~50%, on 32K it reaches ~64%.

---

See also [VRAM_MANAGEMENT.md](VRAM_MANAGEMENT.md) (how the window is auto-fitted at deployment) and [BENCHMARKS.md](BENCHMARKS.md) (measured throughput).
