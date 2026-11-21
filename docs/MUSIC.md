# Music generation

FLAI can turn lyrics and a style prompt into a sung song. The model runs in its own
container (`flai-music`) because it pins its own PyTorch stack; the web application only
speaks its REST API.

| Item | Value |
|---|---|
| Model | ACE-Step 1.5, `acestep-v15-turbo` (2B DiT) + `acestep-5Hz-lm-0.6B` |
| Licence | MIT for code and weights |
| Image | ~9 GB (CUDA runtime + torch); weights are a volume, ~11 GB on first start |
| Enable | `./deploy.sh --with-music`, or `--profile with-music` manually |
| Settings | `MUSIC_URL` (empty disables the module), `MUSIC_TIMEOUT_S`, `MUSIC_MODEL`, `MUSIC_LM_MODEL`, `MUSIC_DEFAULT_DURATION_S`, `MUSIC_MAX_DURATION_S` |

## Requirements

| | GPU host | CPU-only host |
|---|---|---|
| VRAM | 8 GB minimum (6.9 GB measured peak), 16 GB comfortable | — |
| RAM | ~6 GB (offloaded weights) | **15 GB** measured |
| Disk | image 20.8 GB + weights 11 GB | image 11.8 GB + weights 11 GB |
| Image | `flai-music:cuda` (`services/music/Dockerfile`) | `flai-music:cpu` (`Dockerfile.cpu`, CPU torch wheel) |
| Speed, 30 s song | 10–12 s | 4–5 min (~8x real time) |
| Enable | `./deploy.sh --with-music` | `MUSIC_ALLOW_CPU=1 ./deploy.sh --with-music` |

The 8–12 GB tiers run the 2B pair after the chat model is evicted from the card; the music
container releases VRAM when the song is done, so chat reloads on its next message. The XL 4B
tier needs ≥12 GB with offload and 19 GB of weights — it is not part of the default install.

## What it costs

Measured on an RTX 5060 Ti (16 GB) with `nvidia-smi` sampled every 200 ms:

| Measurement | Value |
|---|---|
| Idle after load | 5.5 GB |
| Peak, 30 s song | 6.9 GB |
| 30 s song, wall clock | 10–12 s |
| 45 s song | 14–17 s |
| Model load | ~21 s (in-process); the first song after the container starts takes ~170 s because the tokenizer and the LM initialise too |

The music module is a full GPU task: the queue unloads llama.cpp before it and the
container is restarted afterwards, because its CUDA context is not released by the model
itself. That is the same rule the video pipeline follows.

## What the model needs from the text

Two rules come from measurement, not from taste:

- **Structure tags.** Lyrics must carry `[verse]`/`[chorus]`. Without them the same
  Russian lines produced no intelligible vocal at all (Whisper found no speech); with
  them the words came back at 0.78–0.83 recall.
- **Duration must fit the lyric length.** Six lines at 30 s sang the words; ten lines at
  45 s scored 0.04–0.50, and six lines at 60 s made the planner label the track an
  instrumental and drop the vocals. Roughly 3–5 seconds per line.

The client adds structure tags when the text has none and refuses a duration above
`MUSIC_MAX_DURATION_S` instead of silently clamping it.

## CPU-only hosts

The model runs on CPU, as upstream documents, but it is heavy and slow — measured on a
12-core host with the CUDA-free image:

| Measurement | Value |
|---|---|
| Model init | 74–77 s |
| 10 s song | 80–90 s (~8x real time) |
| 30 s song (extrapolated) | 4–5 min |
| Container RAM | 15 GB |

That competes with the main model on a 16 GB host, so CPU is **opt-in**:

```bash
MUSIC_ALLOW_CPU=1 ./deploy.sh --with-music
```

Without the variable the deploy script refuses with the numbers above. The CPU stack
builds `flai-music:cpu` from `services/music/Dockerfile.cpu` (CPU torch wheel, no CUDA),
and the compose file has no GPU reservation.

## Quality is stochastic

Two runs of the same configuration scored 0.04 and 0.50 recall on the long fixture. A
single take proves nothing: a request that needs a reliable result should generate two
candidates and keep the better one. This is recorded in the local design notes; the
chat path does not do it yet.

## Verification

```bash
docker compose --profile with-music up -d music
docker exec flai-music curl -fsS http://localhost:8001/health
curl -s http://localhost:5000/health | python3 -m json.tool   # "music": "ok"
```

The first start downloads the weights; the container reports `models_initialized: false`
until the first song (models load lazily, ~21 s).
