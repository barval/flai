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

SD_CLI = "/usr/local/bin/sd-cli"

# Lock to serialize generation (one at a time)
_lock = threading.Lock()

# ── CUDA detection ──
_CUDA_AVAILABLE: bool | None = None


def _check_cuda() -> bool:
    global _CUDA_AVAILABLE
    if _CUDA_AVAILABLE is not None:
        return _CUDA_AVAILABLE
    if shutil.which("nvidia-smi"):
        try:
            result = subprocess.run(["nvidia-smi"], capture_output=True, timeout=10)
            _CUDA_AVAILABLE = result.returncode == 0
        except Exception:
            _CUDA_AVAILABLE = False
    else:
        _CUDA_AVAILABLE = False
    logger.info(f"CUDA detected: {_CUDA_AVAILABLE}")
    return _CUDA_AVAILABLE


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


def _build_generate_cmd(prompt, steps, width, height, cfg_scale, seed, flow_shift, sampler, use_gpu, offload_level=0,
                        preview_path=None, preview_interval=2):
    """Build sd-cli command for image generation with specified offload level.

    offload_level:
        0 — all on GPU
        1 — clip/text encoder on CPU (--clip-on-cpu)
        2 — clip + vae on CPU (--clip-on-cpu --vae-on-cpu)
        3 — everything on CPU (--offload-to-cpu)
    preview_path: path to write preview images (enables --preview tae)
    preview_interval: steps between preview updates (default 2)
    """
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
        "cuda",
        "--diffusion-fa",
        "--flow-shift",
        str(flow_shift),
    ]

    if preview_path:
        cmd.extend(["--preview", "tae", "--preview-path", preview_path, "--preview-interval", str(preview_interval)])

    if offload_level == 1:
        cmd.append("--clip-on-cpu")
    elif offload_level == 2:
        cmd.extend(["--clip-on-cpu", "--vae-on-cpu"])
    elif offload_level >= 3:
        cmd.append("--offload-to-cpu")

    if sampler:
        cmd.extend(["--sampler", sampler])
    return cmd


def _run_sd_cli(cmd, log_path, timeout=300, preview_path=None, preview_url=None,
                 user_id=None, session_id=None, task_id=None, total_steps=None):
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

    offload_levels = range(4) if use_gpu else [3]

    for level in offload_levels:
        cmd = _build_generate_cmd(
            prompt, steps, width, height, cfg_scale, seed, flow_shift, sampler, use_gpu, offload_level=level,
            preview_path=preview_path,
        )

        logger.info(f" Running generate (offload_level={level}): {' '.join(cmd[:12])}...")

        result, log_tail = _run_sd_cli(cmd, "/tmp/sd_cli_output.log", timeout=300,
                                       preview_path=preview_path, preview_url=preview_url,
                                       user_id=user_id, session_id=session_id, task_id=task_id,
                                       total_steps=steps)
        if isinstance(result, dict):
            return result
        if log_tail and _is_oom_error(log_tail):
            logger.warning(f" sd-cli OOM at offload_level={level}, retrying with more offload")
            continue
        logger.info(f" sd-cli failed (non-OOM): {log_tail[:300]}")
        return {"error": "Image generation failed"}

    return {"error": "Insufficient VRAM for image generation. Try disabling GPU or reducing image size."}


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
        "--rng",
        "cuda",
        "--diffusion-fa",
        "--strength",
        str(strength),
    ]

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
        offload_levels = range(4) if use_gpu else [3]
        for level in offload_levels:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as out_tmp:
                output_path = out_tmp.name

            cmd = _build_edit_cmd(edit_prompt, src_path, use_gpu, offload_level=level, strength=strength)
            cmd.extend(["-o", output_path])

            logger.info(f" Running edit (offload_level={level}): {' '.join(cmd[:12])}...")

            proc = None
            start_time = time.time()
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
                        if time.time() - start_time > 900:
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
