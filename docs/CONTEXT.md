# Context Window

## Budget allocation

The window is split **by measured need**, not by fixed percentages (`app/context_budget.py`).
Three things are decided before anything is shared:

- a reply reserve proportional to the window — 10 % of it, clamped to 512–4 096 tokens, because a
  4 000-token answer in an 8 192-token window is as wrong as 200 tokens in a 32 768-token one;
- the fixed prompt, measured for the request itself (800 tokens is the estimate used where the prompt
  does not exist yet);
- nothing else: there is no second safety multiplier.

What remains is shared between the documents and the conversation **by what each one needs** — the
corpus size and the session length are measured before a share is granted. Order of allocation: the
query → the recalled SLM facts (at most `SLM_RECALL_LIMIT`, measured) → the fixed prompt → documents
and history by need.

- When both needs fit, each gets what it needs and the rest stays unused. Filling the window because
  it is there is how a request pays for fragments nobody asked for.
- When they do not both fit, the documents take a proportional share, never less than half of the
  room, and the conversation keeps at least `max(256, available / 8)` tokens, capped by what it
  actually holds — so neither side can silence the other.

`RAG_CONTEXT_PERCENT` and `CONTEXT_HISTORY_PERCENT` stopped being the budget: they are optional caps
with `0` meaning automatic. The retrieval side derives its fragment count from the same split
(`app/rag_budget.py`): a corpus that fits the room is covered entirely, the rest of the room deepens
the relevance half, and a corpus larger than the room gives a quarter of the fragments to covering the
documents the relevance half missed. `RAG_TOP_K` is a **ceiling** for the relevance half, not the
literal count.

Measured on the live 16 GB instance — 24 576-token window, 29-document corpus — the allocator placed
43 fragments covering **all 29** documents, 0 unrepresented, about 11.4k of 21.3k available tokens.
The old fixed 30 % share produced 20 fragments from 12 documents on the same corpus.

A small corpus may also need **one document whole**. When the answer is split between two chunks of
one document — the rule in one article, its time window in another — a single relevance chunk cannot
hold it, so the allocator guarantees room for one document of up to `WHOLE_DOCUMENT_MAX_TOKENS`
(2 000) and retrieval passes the best-matching small document in full
(`RagModule._expand_best_document()`). Its chunks are placed in document order at the position of the
hit, the rest of the retrieval keeps whatever room is left, and documents that no longer fit are
reported as unrepresented instead of disappearing silently. Measured on the live instance: the
question "can repair works be done now" retrieved article 4 of the regional noise law (the list of
noisy works) as its only relevant chunk, while the quiet hours sat in article 2, thirteen places down,
and the answer said it could not find the hours. With the whole 10-chunk law (about 1 900 tokens) the
same model answered "not now — quiet hours are 22:00–07:00 on weekdays, article 2".

The multimodal model keeps its own simpler allocator: no search, no SLM and no summary — only the
conversation.

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

---

See also [VRAM_MANAGEMENT.md](VRAM_MANAGEMENT.md) (how the window is auto-fitted at deployment) and [BENCHMARKS.md](BENCHMARKS.md) (measured throughput).
