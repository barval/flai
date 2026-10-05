# Model Benchmarks

## Measured throughput

All numbers are **synthetic `llama-bench` measurements** (llama.cpp build 10603) on an RTX 5060 Ti 16 GB (Blackwell, 448 GB/s): Flash Attention on, q4_0 KV cache, all layers on GPU (`-ngl -1`). Two metrics are reported: **Prompt (pp512)** — throughput for processing a 512-token prompt — and **Generation (tg128)** — throughput for generating 128 tokens, averaged over 3 repetitions after a warmup run. File sizes are the GGUF file sizes. Real-world throughput differs: the FLAI system prompt and chat history enlarge the prompt, and llama-swap shares VRAM between loaded models.

| Model | Type | Quant | File | Prompt (pp512) | Generation (tg128) | Notes |
|-------|------|-------|------|----------------|--------------------|-------|
| **Qwen3.6-35B-A3B** | Reasoning | UD Q2_K_XL (MoE) | 11.44 GiB | 1594 t/s | **107.5 t/s** | **Current reasoning model** — MoE 35B (3B active) |
| gpt-oss-20b | Reasoning | MXFP4 (MoE) | 11.27 GiB | 2052 t/s | 120.1 t/s | Fast but outdated — MoE 3B active |
| gemma-4-26B-A4B-it | Reasoning | UD Q2_K_XL (MoE) | 9.81 GiB | 2749 t/s | 104.5 t/s | MoE 26B (4B active) |
| Qwen3-4B-Instruct-2507 | Reasoning | Q4_K_M | 2.32 GiB | 5520 t/s | 119.0 t/s | Dense 4B — SD text encoder |
| Ternary-Bonsai-27B | Reasoning | Q2_g64 | 7.05 GiB | 993 t/s | 43.3 t/s | Ternary 27B |
| gemma-4-12B-it-qat | Reasoning | QAT Q4_K_XL | 6.24 GiB | 2310 t/s | 48.3 t/s | Dense 12B |
| Qwen3.8-27B | Reasoning | UD Q2_K_XL | 9.14 GiB | 763 t/s | 33.2 t/s | Dense 27B |
| Muse-Glimmer-30B | Reasoning | UD Q2_K_XL | 11.58 GiB | 664 t/s | 26.1 t/s | Dense 30B |
| **Qwen3VL-8B-Instruct** | Multimodal | Q4_K_M | 4.68 GiB | 3343 t/s | **74.8 t/s** | **Current multimodal model** — fastest vision model |
| bge-m3-Q8_0 | Embedding | Q8_0 | 0.60 GiB | 31500 t/s | 550 t/s | Embedding (RAG) only |

> **Current stack: CPU vs GPU (the three models FLAI uses by default).**

Splits the stack by mode: **GPU mode** uses the full-quality models below; **CPU-only mode** uses lightweight replacements — Qwen3VL-4B (multimodal) and gpt-oss-20b-mxfp4 (reasoning); the embedding model is shared.

The CPU column in the table below was measured **live on the previous 12-core CPU-only stack (Qwen3VL-8B + Qwen3.6-35B)** — the CPU models are faster because they are smaller (Qwen3VL-4B) or have CPU-friendly MXFP4 kernels (gpt-oss-20b). The 16 GB column was measured on an RTX 5060 Ti 16 GB (Blackwell, 448 GB/s). The 8/12 GB columns are **estimates** for typical cards of that class — real throughput scales with the card's memory bandwidth and generation, so treat them as guidance, not guarantees.

| Model | Role | File | CPU 12C (prev stack, measured) | GPU 8 GB* | GPU 12 GB* | GPU 16 GB (measured) |
|-------|------|------|--------------------|-----------|-----------|----------------------|
| **Qwen3VL-8B-Instruct-Q4_K_M** | Multimodal (chat/router/vision) | 4.7 GB + mmproj 1.1 GB | **3.7 tok/s** (Qwen3VL-4B on CPU: faster) | 25–35 tok/s | 45–60 tok/s | **73.1 tok/s** |
| **Qwen3.6-35B-A3B-UD-Q2_K_XL** | Reasoning | 12 GB | **9.5 tok/s** (gpt-oss-20b-mxfp4 on CPU) | 15–20 tok/s (partial CPU offload) | 70–90 tok/s | **106.2 tok/s** |
| **bge-m3-Q8_0** | Embedding | 0.6 GB | **~1020 tok/s** (warm, 20 ms/doc) | 1.5–2.5 k tok/s | 2.5–4 k tok/s | ~4 k tok/s |

> **Context windows (auto-fit):** at deployment the seed config automatically fits the context window to the hardware from the GGUF metadata and measured RAM/VRAM — multimodal 32768 on 24/16 GB tiers, 16384 on 8 GB, 8192 in CPU mode (see `app/database.py:_autofit_context()`); reasoning 32768/24576 on 24/16 GB, 16384 on 8 GB, 8192 CPU. The reasoning model's `--reasoning-budget` scales with the fitted window. Multimodal needs ≥16384 for vision token counts.

> **Read the CPU row as follows:** a typical chat answer (~200 tokens) from the multimodal model takes ~55 s on CPU vs ~3 s on a 16 GB GPU; a reasoning answer takes ~21 s on CPU vs ~2 s on GPU. Embedding/vector indexing is the least affected (bge-m3 is small and fast even on CPU).

> **Why MoE models win as reasoning models:** Despite "20B+" parameters, these models use the Mixture-of-Experts (MoE) architecture with several experts — only a small number of parameters (~3B) is active per token. This gives the compute cost of a 3B model with the "knowledge" of a 20B+ model. MoE models are always faster than dense models of the same size.

> **Qwen3.6-35B-A3B for reasoning:** MoE architecture (35B total, ~3B active) delivers **107.5 t/s** — only 10% slower than gpt-oss-20b. The best option when gpt-oss-20b quality is not enough.

> **Why MTP doesn't help on 128-bit GPUs:** Multi-Token Prediction (MTP) predicts draft tokens with a small head, then verifies them in parallel. On high-bandwidth GPUs (256/512-bit), this yields 1.4–2.2× speedup. On RTX 5060 Ti's 128-bit bus (448 GB/s), the draft model's extra memory reads saturate the already-limited bandwidth. MTP accordingly provides no meaningful speedup over a plain Q4_K_M of the same size, so MTP variants are not used.

> **MXFP4 on Blackwell:** RTX 5060 Ti (Blackwell GB206) has 5th-gen Tensor cores with native FP4 hardware support. MXFP4 models achieve near-Q4_K_M quality at similar file sizes while benefiting from Blackwell's optimized FP4 pathways.

---

See also [VRAM_MANAGEMENT.md](VRAM_MANAGEMENT.md) for how a model is fitted to a GPU at deployment time.
