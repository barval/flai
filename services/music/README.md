# Music sidecar — ACE-Step 1.5

Turns lyrics and a style prompt into a sung song. Runs as its own container because
the upstream project pins its own PyTorch stack (`torch 2.10 + cu128`), which must
not enter the web image.

- **Licence:** MIT for the code and the weights (upstream `LICENSE`, model card
  `License: mit`). Pin `ACESTEP_COMMIT` and keep the licence file with the image.
- **Size:** the image is ~9 GB; models are a volume (`./models`, ~11 GB for the 2B
  turbo pair), downloaded on the first start.
- **Measured** on an RTX 5060 Ti (16 GB): idle 5.5 GB, peak 6.9 GB; a 30 s Russian
  song takes 12–15 s with the 2B turbo DiT and the 0.6B LM.

## Run

```bash
docker compose -f services/music/docker-compose.yml up -d --build
curl -s localhost:8001/health   # inside the container; not published to the host
```

Inside the FLAI network the API is `http://flai-music:8001`. Enable the module with:

```env
MUSIC_URL=http://flai-music:8001
```

Starting the whole stack with `--profile with-music` (see `docker-compose.gpu.yml`)
starts this container too.

## Contract

`POST /release_task` → `{"data": {"task_id": ...}}`; `POST /query_result` →
a row whose `result` is a JSON string with `status`/`file`; `GET /v1/audio?path=…`
returns the WAV. FLAI's `modules/music.py` is the only client.

## What the model needs

- Lyrics must carry `[verse]`/`[chorus]` structure. Measured: without tags the same
  Russian lines produced no intelligible vocal; with tags the words came back at
  0.78–0.83 recall through Whisper.
- Duration must match the lyric length. Six short lines at 60 s made the LM planner
  label the track an instrumental and drop the vocals.

The client adds structure tags and validates the duration; anything longer than
`MUSIC_MAX_DURATION_S` is refused instead of clamped.

## Unload

The CUDA context is released by restarting the container, the same rule as the video
pipeline. The queue unloads llama.cpp before a song and re-clears the card after it.
