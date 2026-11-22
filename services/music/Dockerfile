# ACE-Step 1.5 music sidecar (MIT code and weights).
#
# Heavy by nature: the upstream project pins its own PyTorch (torch 2.10 + cu128),
# so it lives in its own image instead of the web image. Models are NOT baked in;
# the first start downloads ~11 GB into the mounted volume.
#
# Measured on an RTX 5060 Ti (16 GB): idle 5.5 GB, peak 6.9 GB, a 30 s Russian
# song in 10-12 s with the 2B turbo DiT and the 0.6B LM.

FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    ACESTEP_CHECKPOINTS_DIR=/app/checkpoints \
    ACESTEP_CONFIG_PATH=acestep-v15-turbo \
    ACESTEP_LM_MODEL_PATH=acestep-5Hz-lm-0.6B \
    ACESTEP_LM_BACKEND=pt \
    ACESTEP_DEVICE=cuda \
    ACESTEP_QUEUE_WORKERS=1 \
    HF_HOME=/app/checkpoints/.hf \
    PATH="/app/.venv/bin:$PATH" \
    ACESTEP_API_HOST=0.0.0.0 \
    PORT=8001

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip curl ffmpeg git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pinned upstream commit: the weights and code move fast, and the licence file
# lives in the same tree. Update deliberately and re-measure after a bump.
ARG ACESTEP_COMMIT=main
RUN --mount=type=cache,target=/root/.cache/uv \
    pip install --no-cache-dir --break-system-packages uv \
    && git clone https://github.com/ace-step/ACE-Step-1.5.git /app \
    && cd /app \
    && git checkout "${ACESTEP_COMMIT}" \
    && uv sync --python 3.12 --no-dev

WORKDIR /app
EXPOSE 8001

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD curl -fsS http://localhost:8001/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
