#!/usr/bin/env python3
"""
Simple HTTP wrapper around sd-cli for stable-diffusion.cpp.
Runs inside the sd container where sd-cli, models, and CUDA are available.
"""

import base64
import contextlib
import json
import logging
import os
import re
import select
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from logging.handlers import RotatingFileHandler
from urllib.parse import urlparse

import requests

# ── Logging with rotation (max 10MB, 5 files) ──
LOG_DIR = os.environ.get("SD_LOG_DIR", "/app/logs")
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("sd-wrapper")
logger.setLevel(logging.INFO)

for log_name in ["sd_generation.log", "sd_edit.log"]:
    handler = RotatingFileHandler(
        os.path.join(LOG_DIR, log_name),
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
    )
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)

# Also log to stdout
stdout_handler = logging.StreamHandler()
stdout_handler.setFormatter(logging.Formatter("[sd-wrapper] %(message)s"))
logger.addHandler(stdout_handler)

# ── Generation model — Z-Image Turbo (only supported model) ──
DEFAULT_DIFFUSION_MODEL = "/app/models/diffusion_models/z_image_turbo-Q8_0.gguf"
DEFAULT_VAE = "/app/models/vae/ae.safetensors"
DEFAULT_LLM = "/app/models/text_encoders/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
DEFAULT_STEPS = 10
DEFAULT_FLOW_SHIFT = 2.0
DEFAULT_SAMPLER = None  # auto for z_image_turbo

# ── Edit model — Flux.2 Klein 4B ──
# Ref: https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/flux2.md
EDIT_DIFFUSION_MODEL = "/app/models/diffusion_models/flux-2-klein-4b-Q8_0.gguf"
EDIT_VAE = "/app/models/vae/flux2_ae.safetensors"
EDIT_LLM = "/app/models/text_encoders/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
EDIT_DEFAULT_STRENGTH = 0.7

# ── Video model — Wan 2.2 TI2V 5B Turbo ──
# sd.cpp needs --diffusion-model for a diffusion-only checkpoint: passing it as
# -m fails with "get sd version from file failed".
WAN_DIFFUSION_MODEL = os.environ.get(
    "WAN_VIDEO_MODEL_PATH", "/app/models/diffusion_models/wan2.2-ti2v-5b-turbo-Q4_K_M.gguf"
)
WAN_VAE = os.environ.get("WAN_VIDEO_VAE_PATH", "/app/models/vae/Wan2.2_VAE.safetensors")
WAN_TEXT_ENCODER = os.environ.get(
    "WAN_VIDEO_TEXT_ENCODER_PATH", "/app/models/text_encoders/umt5-xxl-encoder-Q8_0.gguf"
)
WAN_VIDEO_STEPS = int(os.environ.get("WAN_VIDEO_STEPS", "4"))
WAN_VIDEO_CFG = float(os.environ.get("WAN_VIDEO_CFG_SCALE", "1.0"))
WAN_VIDEO_FLOW_SHIFT = float(os.environ.get("WAN_VIDEO_FLOW_SHIFT", "3.0"))
# Wan 2.2 5B compresses time by 4 frames → valid lengths are 4n+1; 121 = 5 s
# at 24 fps, the length the model was trained on.
WAN_MAX_FRAMES = 121
WAN_VIDEO_TIMEOUT_S = int(os.environ.get("WAN_CLI_TIMEOUT", "1800"))
WAN_VIDEO_CPU_TIMEOUT_S = int(os.environ.get("WAN_CLI_CPU_TIMEOUT", "7200"))
# Below this much VRAM the umt5 encoder is streamed from RAM
# (--offload-to-cpu): it takes ~6 GB on the GPU and would otherwise push a
# 12 GB card into OOM before the first denoising step.
WAN_OFFLOAD_BELOW_VRAM_MB = int(os.environ.get("WAN_OFFLOAD_BELOW_VRAM_MB", "12000"))
DEFAULT_VIDEO_NEGATIVE = (
    "worst quality, inconsistent motion, blurry, jittery, distorted, flickering, "
    "watermark, text, logo, deformed, ugly, oversaturated, underexposed, pixelated, static, frozen"
)
# Falling back one step at a time: each is a valid 4n+1 / 32-multiple preset.
# Length is preserved first (a 5-second clip at a lower resolution is usually
# more useful than a 3-second one at full size).
WAN_VIDEO_FALLBACKS = [(832, 480, 121), (640, 384, 121), (512, 288, 121), (512, 288, 49)]

SD_CLI = "/usr/local/bin/sd-cli"

# Lock to serialize generation (one at a time)
_lock = threading.Lock()

# ── CUDA detection ──
# Preferred backend is baked into the image (SD_BACKEND build arg) — cuda maps
# to nvidia-smi live check, vulkan/cpu are treated as non-CUDA.
_DEFAULT_BACKEND = os.environ.get("SD_BACKEND", "cuda")
_CUDA_AVAILABLE: bool | None = None


def _check_cuda() -> bool:
    global _CUDA_AVAILABLE
    if _CUDA_AVAILABLE is not None:
        return _CUDA_AVAILABLE
    if _DEFAULT_BACKEND != "cuda":
        _CUDA_AVAILABLE = False
    elif shutil.which("nvidia-smi"):
        try:
            result = subprocess.run(["nvidia-smi"], capture_output=True, timeout=10)
            _CUDA_AVAILABLE = result.returncode == 0
        except Exception:
            _CUDA_AVAILABLE = False
    else:
        _CUDA_AVAILABLE = False
    logger.info(f"CUDA detection result: {_CUDA_AVAILABLE} (backend={_DEFAULT_BACKEND})")
    return _CUDA_AVAILABLE


def _cli_timeout(default_s: int) -> int:
    """Allow larger timeouts for slow CPU generation via SD_CLI_TIMEOUT / SD_EDIT_TIMEOUT env."""
    try:
        return int(os.environ.get("SD_CLI_TIMEOUT", default_s))
    except ValueError:
        return default_s


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.info(format % args)

    def handle(self):
        with contextlib.suppress(BrokenPipeError):
            super().handle()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/v1/images/generations":
            self._handle_generation()
        elif path == "/v1/images/edits":
            self._handle_edit()
        elif path == "/v1/video/generations":
            self._handle_video()
        else:
            self.send_error(404, "Not found")

    def _handle_generation(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        result = generate_image(data)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def _handle_edit(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        result = edit_image(data)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def _handle_video(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        result = generate_video(data)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode())
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Stable Diffusion Wrapper is running\n")


def _validate_use_gpu(use_gpu: bool) -> bool:
    """Return True only if user wants GPU AND CUDA is actually available."""
    return use_gpu and _check_cuda()


def _build_generate_cmd(
    prompt,
    steps,
    width,
    height,
    cfg_scale,
    seed,
    flow_shift,
    sampler,
    use_gpu,
    offload_level=0,
    preview_path=None,
    preview_interval=2,
):
    """Build sd-cli command for image generation with specified offload level.

    Backend-aware flags: CUDA builds use `--rng cuda` and flash-attention,
    CPU/Vulkan builds fall back to the cross-platform `--rng std_default`.

    offload_level:
        0 — all on GPU
        1 — clip/text encoder on CPU (--clip-on-cpu)
        2 — clip + vae on CPU (--clip-on-cpu --vae-on-cpu)
        3 — everything on CPU (--offload-to-cpu)
    preview_path: path to write preview images (enables --preview tae)
    preview_interval: steps between preview updates (default 2)
    """
    cuda = _check_cuda()
    cmd = [
        SD_CLI,
        "--diffusion-model",
        DEFAULT_DIFFUSION_MODEL,
        "--vae",
        DEFAULT_VAE,
        "--llm",
        DEFAULT_LLM,
        "-p",
        prompt,
        "--cfg-scale",
        str(cfg_scale),
        "--steps",
        str(steps),
        "-H",
        str(height),
        "-W",
        str(width),
        "--seed",
        str(seed),
        "--rng",
        "cuda" if cuda else "std_default",
        "--flow-shift",
        str(flow_shift),
    ]

    if cuda:
        cmd.append("--diffusion-fa")

    if preview_path:
        cmd.extend(["--preview", "tae", "--preview-path", preview_path, "--preview-interval", str(preview_interval)])

    if cuda:
        if offload_level == 1:
            cmd.append("--clip-on-cpu")
        elif offload_level == 2:
            cmd.extend(["--clip-on-cpu", "--vae-on-cpu"])
        elif offload_level >= 3:
            cmd.append("--offload-to-cpu")

    if sampler:
        cmd.extend(["--sampler", sampler])
    return cmd


def _run_sd_cli(
    cmd,
    log_path,
    timeout=300,
    preview_path=None,
    preview_url=None,
    user_id=None,
    session_id=None,
    task_id=None,
    total_steps=None,
):
    """Run sd-cli subprocess and return (result_dict, tail_log).

    Pipes stdout to parse step progress in real-time and POSTs to preview_url.
    Also writes stdout to log_path for debugging.
    """
    proc = None
    output_path = None
    start_time = time.time()
    try:
        for part in cmd:
            if part.startswith("/") and part.endswith(".png") and os.path.dirname(part):
                output_path = part
                break
        if not output_path:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                output_path = tmp.name
            cmd.extend(["-o", output_path])
        with open(log_path, "a") as log_file:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
            stdout_buf = b""
            while proc.poll() is None:
                ready, _, _ = select.select([proc.stdout], [], [], 0.5)
                if ready:
                    byte = proc.stdout.read(1)
                    if byte:
                        stdout_buf += byte
                        if byte in (b"\r", b"\n"):
                            line = stdout_buf.decode(errors="replace")
                            log_file.write(line)
                            log_file.flush()
                            if preview_url:
                                m = re.search(r"(\d+)/(\d+)", line)
                                if m:
                                    step, total = int(m.group(1)), int(m.group(2))
                                    # Skip text-encoder token progress (e.g. 1/1095)
                                    if total_steps and total != total_steps:
                                        pass
                                    else:
                                        try:
                                            requests.post(
                                                preview_url,
                                                json={
                                                    "user_id": user_id,
                                                    "session_id": session_id,
                                                    "task_id": task_id,
                                                    "step": step,
                                                    "total": total,
                                                },
                                                timeout=2,
                                            )
                                        except Exception as e:
                                            logger.warning(f"Failed to post step progress: {e}")
                            stdout_buf = b""
                if time.time() - start_time > timeout:
                    proc.kill()
                    proc.wait()
                    return {"error": "sd-cli timeout"}, ""
            remaining = proc.stdout.read()
            if remaining:
                log_file.write(remaining.decode(errors="replace"))
                log_file.flush()
            if proc.returncode != 0:
                try:
                    with open(log_path) as f:
                        log_tail = f.read()[-2000:]
                except Exception:
                    log_tail = ""
                return None, log_tail
            if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                return {"error": "sd-cli produced empty output"}, ""
            with open(output_path, "rb") as f:
                image_bytes = f.read()
            return {"created": 0, "data": [{"b64_json": base64.b64encode(image_bytes).decode()}]}, ""
    except subprocess.TimeoutExpired:
        return {"error": "sd-cli timeout"}, ""
    except Exception as e:
        return {"error": str(e)}, ""
    finally:
        if proc and proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.kill()
        if output_path and os.path.exists(output_path):
            with contextlib.suppress(Exception):
                os.remove(output_path)


def _is_oom_error(log_tail):
    """Check if sd-cli log indicates an OOM error."""
    return "out of memory" in log_tail.lower() or "cudamalloc" in log_tail.lower() or "cuda error" in log_tail.lower()


def generate_image(data):
    prompt = data.get("prompt", "")
    steps = int(data.get("steps", DEFAULT_STEPS))
    width = int(data.get("width", 1024))
    height = int(data.get("height", 1024))
    cfg_scale = float(data.get("cfg_scale", 1.0))
    seed = int(data.get("seed", -1))
    flow_shift = float(data.get("flow_shift", DEFAULT_FLOW_SHIFT))
    sampler = data.get("sampling_method", DEFAULT_SAMPLER)
    use_gpu = _validate_use_gpu(data.get("use_gpu", False))
    preview_path = data.get("preview_path")
    preview_url = data.get("preview_url")
    user_id = data.get("user_id")
    session_id = data.get("session_id")
    task_id = data.get("task_id")

    start_level = int(data.get("start_offload_level", 0)) if use_gpu else 3
    offload_levels = range(start_level, 4)

    for level in offload_levels:
        cmd = _build_generate_cmd(
            prompt,
            steps,
            width,
            height,
            cfg_scale,
            seed,
            flow_shift,
            sampler,
            use_gpu,
            offload_level=level,
            preview_path=preview_path,
        )

        logger.info(f" Running generate (offload_level={level}): {' '.join(cmd[:12])}...")

        result, log_tail = _run_sd_cli(
            cmd,
            "/tmp/sd_cli_output.log",
            # A GPU generation of the seeded Q8_0 z_image_turbo takes ~180 s, so
            # the old hard 300 s cut a healthy run short as soon as the host was
            # loaded (live defect: a 301 s run died as "sd-cli timeout" though
            # nothing was wrong with the request). 600 s keeps the guard BELOW
            # FLAI's own SD_CPP_TIMEOUT (900 s), so this wrapper still reports the
            # failure itself instead of the client giving up first; the
            # SD_CLI_TIMEOUT env var overrides the default.
            timeout=_cli_timeout(600) if _check_cuda() else _cli_timeout(1800),
            preview_path=preview_path,
            preview_url=preview_url,
            user_id=user_id,
            session_id=session_id,
            task_id=task_id,
            total_steps=steps,
        )
        if isinstance(result, dict):
            return result
        if log_tail and _is_oom_error(log_tail):
            logger.warning(f" sd-cli OOM at offload_level={level}, retrying with more offload")
            continue
        logger.info(f" sd-cli failed (non-OOM): {log_tail[:300]}")
        return {"error": "Image generation failed"}

    return {"error": "Insufficient VRAM for image generation. Try disabling GPU or reducing image size."}


def _vram_total_mb() -> int:
    """Total VRAM of the first visible GPU (0 when unknown)."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return int(result.stdout.split()[0])
    except Exception:
        return 0


def _snap_frames(num_frames: int) -> int:
    """Wan needs 4n+1 frames (VAE temporal stride 4), at most 121."""
    frames = max(5, min(int(num_frames), WAN_MAX_FRAMES))
    return ((frames - 1) // 4) * 4 + 1


def _snap_dim(value: int) -> int:
    """Both sides must be multiples of 32; sd.cpp pads and crops otherwise."""
    return max(256, (int(value) // 32) * 32)


def _build_video_cmd(
    prompt,
    negative_prompt,
    width,
    height,
    num_frames,
    frame_rate,
    steps,
    cfg_scale,
    flow_shift,
    seed,
    init_image=None,
    use_gpu=True,
):
    """Build the sd-cli command for one Wan 2.2 clip."""
    cmd = [
        SD_CLI,
        "-M",
        "vid_gen",
        "--diffusion-model",
        WAN_DIFFUSION_MODEL,
        "--vae",
        WAN_VAE,
        "--t5xxl",
        WAN_TEXT_ENCODER,
        "-p",
        prompt,
        "-n",
        negative_prompt,
        "-W",
        str(width),
        "-H",
        str(height),
        "--video-frames",
        str(num_frames),
        "--fps",
        str(frame_rate),
        "--steps",
        str(steps),
        "--cfg-scale",
        str(cfg_scale),
        "--sampling-method",
        "euler",
        "--flow-shift",
        str(flow_shift),
        "--seed",
        str(seed),
        "--vae-tiling",
    ]
    if use_gpu:
        cmd.append("--diffusion-fa")
        total_vram = _vram_total_mb()
        if 0 < total_vram < WAN_OFFLOAD_BELOW_VRAM_MB:
            # Keep the weights in RAM and stream them per step: slower, but the
            # encoder alone is ~6 GB and the clip would not fit otherwise.
            cmd.append("--offload-to-cpu")
    if init_image:
        cmd.extend(["-i", init_image])
    return cmd


def _convert_to_mp4(avi_path: str, mp4_path: str) -> None:
    """sd-cli writes avi/webm only — the chat player needs mp4 (h264)."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            avi_path,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            mp4_path,
        ],
        check=True,
        timeout=600,
        capture_output=True,
    )


def _run_video_cli(cmd, timeout, log_path="/tmp/sd_video.log"):
    """Run sd-cli in video mode and return (mp4_bytes, error_log_tail)."""
    workdir = tempfile.mkdtemp(prefix="sd-video-")
    avi_path = os.path.join(workdir, "clip.avi")
    mp4_path = os.path.join(workdir, "clip.mp4")
    try:
        full = [*cmd, "-o", avi_path]
        logger.info(f" Running video generation: {' '.join(full[:10])}...")
        try:
            completed = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, "sd-cli timeout"
        with open(log_path, "a") as log_file:
            log_file.write(completed.stdout or "")
            log_file.write(completed.stderr or "")
        if completed.returncode != 0:
            return None, ((completed.stdout or "") + (completed.stderr or ""))[-2000:]
        if not os.path.exists(avi_path) or os.path.getsize(avi_path) == 0:
            return None, "sd-cli produced empty video"
        if not shutil.which("ffmpeg"):
            return None, "ffmpeg is not installed in the sd.cpp image — cannot produce mp4"
        _convert_to_mp4(avi_path, mp4_path)
        with open(mp4_path, "rb") as f:
            return f.read(), ""
    except Exception as e:
        return None, str(e)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def generate_video(data):
    """Generate one clip with Wan 2.2 TI2V 5B (text or image + text)."""
    with _lock:
        return _generate_video_impl(data)


def _generate_video_impl(data):
    prompt = str(data.get("prompt", "")).strip()
    if not prompt:
        return {"error": "Video prompt is empty"}

    width = _snap_dim(int(data.get("width", 832)))
    height = _snap_dim(int(data.get("height", 480)))
    num_frames = _snap_frames(int(data.get("num_frames", WAN_MAX_FRAMES)))
    frame_rate = int(data.get("frame_rate", 24))
    steps = int(data.get("steps", WAN_VIDEO_STEPS))
    cfg_scale = float(data.get("cfg_scale", WAN_VIDEO_CFG))
    flow_shift = float(data.get("flow_shift", WAN_VIDEO_FLOW_SHIFT))
    seed = int(data.get("seed", -1))
    negative_prompt = str(data.get("negative_prompt") or DEFAULT_VIDEO_NEGATIVE)
    use_gpu = _validate_use_gpu(data.get("use_gpu", True))
    timeout = _cli_timeout(WAN_VIDEO_TIMEOUT_S) if use_gpu else WAN_VIDEO_CPU_TIMEOUT_S

    src_dir = None
    init_image = None
    if data.get("image_data"):
        try:
            src_dir = tempfile.mkdtemp(prefix="sd-video-src-")
            init_image = os.path.join(src_dir, "source.jpg")
            with open(init_image, "wb") as f:
                f.write(base64.b64decode(data["image_data"]))
        except Exception as e:
            logger.warning(f" Could not decode source image: {e}")
            init_image = None

    # Step down the preset chain on OOM, exactly like image generation does with
    # offload levels: a 720p request on a 12 GB card still returns a clip.
    candidates = [(width, height, num_frames)]
    for fallback in WAN_VIDEO_FALLBACKS:
        if fallback not in candidates and fallback[0] <= width and fallback[2] <= num_frames:
            candidates.append(fallback)

    try:
        return _run_video_candidates(
            candidates,
            prompt,
            negative_prompt,
            frame_rate,
            steps,
            cfg_scale,
            flow_shift,
            seed,
            init_image,
            use_gpu,
            timeout,
        )
    finally:
        if src_dir:
            shutil.rmtree(src_dir, ignore_errors=True)


def _run_video_candidates(
    candidates,
    prompt,
    negative_prompt,
    frame_rate,
    steps,
    cfg_scale,
    flow_shift,
    seed,
    init_image,
    use_gpu,
    timeout,
):
    """Try each preset in turn; step down only on an out-of-memory error."""
    for cand_w, cand_h, cand_frames in candidates:
        cmd = _build_video_cmd(
            prompt,
            negative_prompt,
            cand_w,
            cand_h,
            cand_frames,
            frame_rate,
            steps,
            cfg_scale,
            flow_shift,
            seed,
            init_image=init_image,
            use_gpu=use_gpu,
        )
        started = time.time()
        video_bytes, log_tail = _run_video_cli(cmd, timeout)
        elapsed = round(time.time() - started, 1)
        if video_bytes:
            logger.info(
                f" Video ready: {cand_w}×{cand_h}×{cand_frames} in {elapsed}s "
                f"({len(video_bytes) // 1024} KB)"
            )
            return {
                "created": int(time.time()),
                "video_data": base64.b64encode(video_bytes).decode(),
                "file_name": f"{time.strftime('%Y-%m-%d_%H-%M-%S')}_wan.mp4",
                "file_size": len(video_bytes),
                "file_type": "video/mp4",
                "generation_time": elapsed,
                "seed": seed,
                "metadata": {
                    "backend": "wan",
                    "model": os.path.basename(WAN_DIFFUSION_MODEL),
                    "width": cand_w,
                    "height": cand_h,
                    "num_frames": cand_frames,
                    "frame_rate": frame_rate,
                    "steps": steps,
                    "cfg_scale": cfg_scale,
                    "flow_shift": flow_shift,
                },
            }
        if log_tail and _is_oom_error(log_tail):
            logger.warning(f" Video OOM at {cand_w}×{cand_h}×{cand_frames}, stepping down")
            continue
        logger.info(f" Video generation failed (non-OOM): {(log_tail or '')[:300]}")
        return {"error": "Video generation failed"}

    return {"error": "Insufficient VRAM for video generation. Try a smaller size."}


def edit_image(data):
    """Edit an existing image using Flux.2 Klein 4B model."""
    with _lock:
        return _edit_image_impl(data)


def _build_edit_cmd(edit_prompt, src_path, use_gpu, offload_level=0, strength=0.7):
    """Build sd-cli command for image editing with specified offload level.

    offload_level:
        0 — all on GPU
        1 — clip/text encoder on CPU (--clip-on-cpu)
        2 — clip + vae on CPU (--clip-on-cpu --vae-on-cpu)
        3 — everything on CPU (--offload-to-cpu)
    """
    cmd = [
        SD_CLI,
        "--diffusion-model",
        EDIT_DIFFUSION_MODEL,
        "--vae",
        EDIT_VAE,
        "--llm",
        EDIT_LLM,
        "-p",
        edit_prompt,
        "--cfg-scale",
        "1.0",
        "--steps",
        "4",
        "--sampling-method",
        "euler",
        "-r",
        src_path,
        "--seed",
        "-1",
        "--strength",
        str(strength),
    ]

    cuda = _check_cuda()
    cmd.extend(["--rng", "cuda" if cuda else "std_default"])
    if cuda:
        cmd.append("--diffusion-fa")

    if cuda:
        if offload_level == 1:
            cmd.append("--clip-on-cpu")
        elif offload_level == 2:
            cmd.extend(["--clip-on-cpu", "--vae-on-cpu"])
        elif offload_level >= 3:
            cmd.extend(["--offload-to-cpu", "--vae-on-cpu", "--clip-on-cpu"])

    cmd.extend(["--cache-mode", "ucache"])
    return cmd


def _edit_image_impl(data):
    edit_prompt = data.get("edit_prompt", "")
    image_data = data.get("image_data", "")
    use_gpu = _validate_use_gpu(data.get("use_gpu", False))
    preview_url = data.get("preview_url")
    user_id = data.get("user_id")
    session_id = data.get("session_id")
    task_id = data.get("task_id")
    strength = float(data.get("strength", 0.7))
    edit_total_steps = 4  # hardcoded in _build_edit_cmd

    if not edit_prompt:
        return {"error": "No edit prompt provided"}
    if not image_data:
        return {"error": "No source image provided"}

    # Save source image to temp file
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as src_tmp:
        src_tmp.write(base64.b64decode(image_data))
        src_path = src_tmp.name

    try:
        start_level = int(data.get("start_offload_level", 0)) if use_gpu else 3
        offload_levels = range(start_level, 4)
        for level in offload_levels:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as out_tmp:
                output_path = out_tmp.name

            cmd = _build_edit_cmd(edit_prompt, src_path, use_gpu, offload_level=level, strength=strength)
            cmd.extend(["-o", output_path])

            logger.info(f" Running edit (offload_level={level}): {' '.join(cmd[:12])}...")

            proc = None
            start_time = time.time()
            edit_timeout = 900 if _check_cuda() else _cli_timeout(5400)
            try:
                with open("/tmp/sd_cli_edit_output.log", "a") as log_file:
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
                    stdout_buf = b""
                    while proc.poll() is None:
                        ready, _, _ = select.select([proc.stdout], [], [], 0.5)
                        if ready:
                            byte = proc.stdout.read(1)
                            if byte:
                                stdout_buf += byte
                                if byte in (b"\r", b"\n"):
                                    line = stdout_buf.decode(errors="replace")
                                    log_file.write(line)
                                    log_file.flush()
                                    if preview_url:
                                        m = re.search(r"(\d+)/(\d+)", line)
                                        if m:
                                            step, total = int(m.group(1)), int(m.group(2))
                                            # Skip text-encoder token progress (e.g. 1/1095)
                                            if total != edit_total_steps:
                                                pass
                                            else:
                                                try:
                                                    requests.post(
                                                        preview_url,
                                                        json={
                                                            "user_id": user_id,
                                                            "session_id": session_id,
                                                            "task_id": task_id,
                                                            "step": step,
                                                            "total": total,
                                                        },
                                                        timeout=2,
                                                    )
                                                except Exception as e:
                                                    logger.warning(f"Failed to post edit step progress: {e}")
                                    stdout_buf = b""
                        if time.time() - start_time > edit_timeout:
                            proc.kill()
                            proc.wait()
                            return {"error": "sd-cli edit timeout"}
                    remaining = proc.stdout.read()
                    if remaining:
                        log_file.write(remaining.decode(errors="replace"))
                        log_file.flush()
                if proc.returncode != 0:
                    try:
                        with open("/tmp/sd_cli_edit_output.log") as f:
                            log_tail = f.read()[-2000:]
                    except Exception:
                        log_tail = ""
                    logger.info(f" sd-cli edit failed (rc={proc.returncode}): {log_tail[:300]}")
                    if _is_oom_error(log_tail):
                        logger.warning(f" sd-cli edit OOM at offload_level={level}, retrying")
                        continue
                    if "timeout" in log_tail.lower():
                        return {"error": "Image editing timeout"}
                    return {"error": "Image editing failed"}

                if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                    return {"error": "Image editing produced empty output"}

                with open(output_path, "rb") as f:
                    image_bytes = f.read()

                logger.info(f" Edit completed: {len(image_bytes)} bytes")
                return {"created": 0, "data": [{"b64_json": base64.b64encode(image_bytes).decode()}]}
            except subprocess.TimeoutExpired:
                return {"error": "sd-cli edit timeout"}
            except Exception as e:
                return {"error": str(e)}
            finally:
                if proc and proc.poll() is None:
                    with contextlib.suppress(Exception):
                        proc.kill()
                if os.path.exists(output_path):
                    with contextlib.suppress(Exception):
                        os.remove(output_path)

        return {"error": "Insufficient VRAM for image editing. Try reducing the image size or closing other GPU tasks."}
    finally:
        if os.path.exists(src_path):
            with contextlib.suppress(Exception):
                os.remove(src_path)


class BlockingHTTPServer(HTTPServer):
    """Handle requests sequentially (no threading) to avoid GPU conflicts."""

    allow_reuse_address = True


if __name__ == "__main__":
    server = BlockingHTTPServer(("0.0.0.0", 7861), Handler)
    logger.info(" Listening on :7861")
    server.serve_forever()
