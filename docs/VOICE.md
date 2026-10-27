# Voice: Transcription and Speech Synthesis


## Whisper ASR

Uses `onerahmet/openai-whisper-asr-webservice` (faster_whisper engine).

A voice message may be recorded on top of attached images: the transcript task carries the images, so the answer is re-queued as an image-chat task and the model sees both what you said and what you attached (voice in the legacy attachment slot and in the multi-attachment layout both pair correctly).

A message may carry several audio items at once — one microphone recording plus audio files, up to `MAX_CHAT_AUDIO` (4) in total (1 voice + 3 files, or 4 files). Every item is transcribed in order (the recording first, then the attachments in order), each transcript is saved as its own assistant message and labelled with the file name when several items ride the message, and the model's question is your typed text followed by all transcripts. An audio message is always answered: the answer continues as a document task (`doc_chat`) when a document is attached, as an image task when an image is, otherwise as a plain text task — a plain audio file next to a typed question used to be transcribed and left unanswered. Tasks share the same cap and the same transcription order.

```bash
# Enable voice features (Whisper ASR; choose ONE TTS backend profile — with-voice-piper or with-voice-kokoro)
docker compose -f docker-compose.gpu.yml --profile with-voice-piper up -d
```

### Whisper on the GPU (optional)

CPU is the default and needs no VRAM. The ASR part is selected by its own profile,
separate from the TTS backend:

```bash
# CPU (default, unchanged): Whisper CPU + Piper
docker compose -f docker-compose.gpu.yml --profile with-voice-piper up -d
# or the ASR part alone, then a TTS backend of your choice
docker compose -f docker-compose.gpu.yml --profile with-whisper-cpu --profile with-tts-piper up -d

# GPU: Whisper on the card + Piper, without the CPU ASR container
docker compose -f docker-compose.gpu.yml \
  --profile with-whisper-gpu --profile with-tts-piper up -d
# then WHISPER_GPU=true in .env and restart flai-web
```

The two ASR containers have different names (`flai-whisper`, `flai-whisper-gpu`)
because compose profiles are additive: a second service cannot reuse a container
name, and selecting both families would otherwise put two servers on one port.
The app picks the endpoint from `WHISPER_GPU` unless `WHISPER_API_URL` is set
explicitly. With the GPU profile the transcription takes `_gpu_lock`, calls
`ensure_vram_for("whisper")` with the `WHISPER_VRAM_MB` budget, and the container
counts as a resident VRAM consumer whose eviction is a restart (only that releases
its CUDA context). Keep `WHISPER_GPU=false` unless chat and Whisper fit together.

### Cost of the voice services (measured)

| Service | Runs on | Model residency | Measured |
|---|---|---|---|
| Whisper (`medium`, faster_whisper) | **CPU**, 6 cores / 3 GB | loaded once in the container | 12 s of speech → **5.2 s** (RTF 0.42); on the old 2-core limit it was 11.7 s |
| Piper | **CPU**, 2 cores / 1 GB | loaded once | 12 s of speech → **2.0–2.5 s** (RTF 0.17–0.20) |
| Kokoro | **CPU** (`map_location="cpu"`), 6 GB | loaded once | one phrase well under a second |

None of them takes a GPU slot, `_gpu_lock` or VRAM: they are ordinary CPU
services, and the queue still serialises them, so two transcriptions never run
at once.

**Should Whisper run on a GPU when the card allows it?** It would cut RTF to
~0.05–0.1 (a one-minute message: ~25 s now, ~3–6 s then), but it needs three
things beyond flipping a flag: the NVIDIA runtime on the container, a CUDA build
of `ctranslate2` (the current image is CPU-oriented, `ASR_DEVICE=cpu`), and the
transcription routed through the GPU queue (`_gpu_lock` + `ensure_vram_for`) so a
voice message cannot collide with the chat or reasoning model. On a 16 GB card
`medium` (~2 GB) could live next to the chat model, not next to the reasoning
model, so the arbiter would evict it between turns. With the 6-core budget above,
CPU latency is one wait per voice message; the GPU path is worth it only for
long recordings and only as an opt-in profile.

Piper and Kokoro are not worth moving to the GPU at all: Piper is a small ONNX
model already running faster than real time, and Kokoro's 82M-parameter model
synthesises a phrase in well under a second on CPU — the GPU would save
milliseconds while taking VRAM the chat model needs.

## Piper TTS

Uses ONNX Piper models for text-to-speech.

```bash
# Download voice models (see services/piper/download-voices.sh)
mkdir -p services/piper/piper_models

# English (male)
curl -L -o services/piper/piper_models/en_US-ryan-medium.onnx \
  "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx"

# Russian (male)
curl -L -o services/piper/piper_models/ru_RU-dmitri-medium.onnx \
  "https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx"
```

## Kokoro TTS

Higher-quality backend (ElevenLabs-level). Selected with `--with-voice-kokoro` / `--profile with-voice-kokoro`. Models are downloaded in one step by `services/kokoro/download-model.sh` (the deploy script runs it automatically):

```bash
bash services/kokoro/download-model.sh
```

## Choosing the backend: Piper vs Kokoro

| Criterion | Piper (default) | Kokoro |
|-----------|-----------------|--------|
| Model size on disk | ~0.24 GB (4 medium voices) | ~0.95 GB (3 model files + voices + espeak-data) |
| Service memory limit | 512 MB | 6 GB |
| Idle RAM (no TTS activity) | ~200–300 MB | ~1.6 GB (light worker, RUAccent not loaded) |
| RAM during active sessions | ~500 MB (all 4 voices cached) | ~3–5.5 GB (RUAccent + model in worker; peak during long phrases) |
| Russian quality | Good (WER 4.38%) | Higher (WER 2.50%, studio actors) |
| Russian voices | `dmitri` (male), `irina` (female) | `dima` (male), `sveta` (female) |
| Russian pronunciation | espeak-ng phonemes, no real word stress | RUAccent: lexical stress, yo restoration, akanye, orthoepy |
| First phrase (fresh container) | ~0.8 s | **~2 s** — a background warmup (one full ru synthesis) runs right after container start (`KOKORO_WARMUP_G2P=1`, default on) |
| Subsequent phrases (same session) | ~0.7–0.8 s | RU ~1.1 s |
| First RU phrase after idle | none — voices stay cached | Depends on `KOKORO_G2P_IDLE_TIMEOUT` (default 300 s): after it expires without a Russian request the RUAccent G2P worker (~3.1 GB) is auto-killed to return RAM, and the next Russian phrase reloads it (~10–11.5 s). Set `0` to never unload (always warm, +3.1 GB RAM permanently). EN is not affected. |

> **Memory notes (measured):** Piper caches every used voice in memory — with all 4 voices loaded it reaches ~497 MiB (limit 1 GB). Kokoro holds ~4.5 GB after its startup warmup (model + RUAccent worker) and releases ~3.1 GB to the OS after `KOKORO_G2P_IDLE_TIMEOUT` seconds without Russian TTS (default 300 s) — a quiet period is followed by a single slower first Russian phrase (~10–11.5 s, then ~1.1 s).

#### Kokoro tuning parameters (.env)

| Parameter | Default | Effect |
|-----------|---------|--------|
| `KOKORO_WARMUP_G2P` | `1` | Runs one full ru synthesis in the background right after container start. With it, the first Russian phrase after deployment takes ~2 s instead of ~55 s. The port is up immediately, so a user clicking during warmup simply takes the regular cold path. Disable (`0`) to keep idle RAM at ~1.6 GB. |
| `KOKORO_G2P_IDLE_TIMEOUT` | `300` | Seconds without a Russian request before the RUAccent G2P worker (~3.1 GB) is terminated to free RAM. Trade-off: longer = always-warm Russian synthesis (no ~10 s reload penalty) at the price of permanently higher RAM; `0` = never unload. |
| `KOKORO_TIMEOUT` | `60` | Web-app client timeout for one synthesis request (code default in `app/config.py`). Covers a cold RUAccent load (~10–20 s) plus model reload under host load; raise it (e.g. to `120`) if cold starts come close to the limit and cause client timeouts. |

---
