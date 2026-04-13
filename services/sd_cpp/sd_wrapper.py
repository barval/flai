#!/usr/bin/env python3
"""
Simple HTTP wrapper around sd-cli for stable-diffusion.cpp.
Runs inside the sd container where sd-cli, models, and CUDA are available.
"""

import json
import subprocess
import tempfile
import os
import base64
import threading
import logging
from logging.handlers import RotatingFileHandler
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# ── Logging with rotation (max 10MB, 5 files) ──
LOG_DIR = os.environ.get('SD_LOG_DIR', '/app/logs')
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger('sd-wrapper')
logger.setLevel(logging.INFO)

for log_name in ['sd_generation.log', 'sd_edit.log']:
    handler = RotatingFileHandler(
        os.path.join(LOG_DIR, log_name),
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5
    )
    handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    logger.addHandler(handler)

# Also log to stdout
stdout_handler = logging.StreamHandler()
stdout_handler.setFormatter(logging.Formatter('[sd-wrapper] %(message)s'))
logger.addHandler(stdout_handler)

# ── Generation model — Z-Image Turbo (only supported model) ──
DEFAULT_DIFFUSION_MODEL = '/app/models/diffusion_models/z_image_turbo-Q8_0.gguf'
DEFAULT_VAE = '/app/models/vae/ae.safetensors'
DEFAULT_LLM = '/app/models/text_encoders/Qwen3-4B-Instruct-2507-Q4_K_M.gguf'
DEFAULT_STEPS = 10
DEFAULT_FLOW_SHIFT = 2.0
DEFAULT_SAMPLER = None  # auto for z_image_turbo

# ── Edit model — Flux.2 Klein 4B ──
# Ref: https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/flux2.md
EDIT_DIFFUSION_MODEL = '/app/models/diffusion_models/flux-2-klein-4b-Q8_0.gguf'
EDIT_VAE = '/app/models/vae/flux2_ae.safetensors'
EDIT_LLM = '/app/models/text_encoders/Qwen3-4B-Instruct-2507-Q4_K_M.gguf'
EDIT_DEFAULT_STRENGTH = 0.7

SD_CLI = '/usr/local/bin/sd-cli'

# Lock to serialize generation (one at a time)
_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.info(format % args)

    def handle(self):
        try:
            super().handle()
        except BrokenPipeError:
            pass  # Client disconnected, ignore

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/v1/images/generations':
            self._handle_generation()
        elif path == '/v1/images/edits':
            self._handle_edit()
        else:
            self.send_error(404, 'Not found')

    def _handle_generation(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, 'Invalid JSON')
            return

        result = generate_image(data)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def _handle_edit(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, 'Invalid JSON')
            return

        result = edit_image(data)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok'}).encode())
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Stable Diffusion Wrapper is running\n')


def generate_image(data):
    prompt = data.get('prompt', '')
    steps = int(data.get('steps', DEFAULT_STEPS))
    width = int(data.get('width', 1024))
    height = int(data.get('height', 1024))
    cfg_scale = float(data.get('cfg_scale', 1.0))
    seed = int(data.get('seed', -1))
    flow_shift = float(data.get('flow_shift', DEFAULT_FLOW_SHIFT))
    sampler = data.get('sampling_method', DEFAULT_SAMPLER)

    cmd = [
        SD_CLI,
        '--diffusion-model', DEFAULT_DIFFUSION_MODEL,
        '--vae', DEFAULT_VAE,
        '--llm', DEFAULT_LLM,
        '-p', prompt,
        '--cfg-scale', str(cfg_scale),
        '--steps', str(steps),
        '-H', str(height),
        '-W', str(width),
        '--seed', str(seed),
        '--rng', 'cuda',
        '--diffusion-fa',
        '--flow-shift', str(flow_shift),
        '--offload-to-cpu',
    ]

    # Add sampler if specified
    if sampler:
        cmd.extend(['--sampler', sampler])

    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
        output_path = tmp.name

    cmd.extend(['-o', output_path])

    logger.info(f" Running: {' '.join(cmd[:12])}...")

    try:
        with open('/tmp/sd_cli_output.log', 'a') as log_file:
            result = subprocess.run(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                timeout=300
            )
        if result.returncode != 0:
            # Log full error for debugging
            try:
                with open('/tmp/sd_cli_output.log', 'r') as f:
                    log_tail = f.read()[-2000:]
            except Exception:
                log_tail = '(no log available)'
            logger.info(f" sd-cli failed (rc={result.returncode}): {log_tail[:500]}")
            # Return user-friendly message only
            return {'error': 'Image generation failed'}

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return {'error': 'Image generation produced empty output'}

        with open(output_path, 'rb') as f:
            image_bytes = f.read()

        return {
            'created': 0,
            'data': [{'b64_json': base64.b64encode(image_bytes).decode()}]
        }
    except subprocess.TimeoutExpired:
        return {'error': 'sd-cli timeout'}
    except Exception as e:
        return {'error': str(e)}
    finally:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass


def edit_image(data):
    """Edit an existing image using Flux.2 Klein 4B model."""
    with _lock:
        return _edit_image_impl(data)


def _edit_image_impl(data):
    edit_prompt = data.get('edit_prompt', '')
    image_data = data.get('image_data', '')  # base64 encoded source image

    if not edit_prompt:
        return {'error': 'No edit prompt provided'}
    if not image_data:
        return {'error': 'No source image provided'}

    # Save source image to temp file
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as src_tmp:
        src_tmp.write(base64.b64decode(image_data))
        src_path = src_tmp.name

    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as out_tmp:
        output_path = out_tmp.name

    # Flux.2 Klein 4B edit parameters
    # Ref: https://github.com/leejet/stable-diffusion.cpp/blob/master/docs/flux2.md
    # Flux Kontext mode: --cfg-scale 1.0, --steps 4, -r for reference image
    cmd = [
        SD_CLI,
        '--diffusion-model', EDIT_DIFFUSION_MODEL,
        '--vae', EDIT_VAE,
        '--llm', EDIT_LLM,
        '-p', edit_prompt,
        '--cfg-scale', '1.0',
        '--steps', '4',
        '--sampling-method', 'euler',
        '-r', src_path,
        '--seed', '-1',
        '--rng', 'cuda',
        '--diffusion-fa',
        '--offload-to-cpu',
        # Force VAE and CLIP to RAM (saves VRAM)
        '--vae-on-cpu',
        '--clip-on-cpu',
        # Use unified cache for better memory management
        '--cache-mode', 'ucache',
        '-o', output_path,
    ]

    logger.info(f" Running edit: {' '.join(cmd[:12])}...")

    try:
        with open('/tmp/sd_cli_edit_output.log', 'a') as log_file:
            result = subprocess.run(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                timeout=900  # 15 minutes for edit operations
            )
        if result.returncode != 0:
            try:
                with open('/tmp/sd_cli_edit_output.log', 'r') as f:
                    log_tail = f.read()[-2000:]
            except Exception:
                log_tail = '(no log available)'
            logger.info(f" sd-cli edit failed (rc={result.returncode}): {log_tail[:500]}")
            # Return detailed error to caller
            if 'out of memory' in log_tail.lower() or 'cudaMalloc failed' in log_tail:
                return {'error': 'Insufficient VRAM (VRAM) for image editing. Try reducing the image size or closing other GPU tasks.'}
            elif 'timeout' in log_tail.lower():
                return {'error': 'Image editing timeout'}
            else:
                return {'error': 'Image editing failed'}

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return {'error': 'Image editing produced empty output'}

        with open(output_path, 'rb') as f:
            image_bytes = f.read()

        logger.info(f" Edit completed: {len(image_bytes)} bytes")
        return {
            'created': 0,
            'data': [{'b64_json': base64.b64encode(image_bytes).decode()}]
        }
    except subprocess.TimeoutExpired:
        return {'error': 'sd-cli edit timeout'}
    except Exception as e:
        return {'error': str(e)}
    finally:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass
        if os.path.exists(src_path):
            try:
                os.remove(src_path)
            except Exception:
                pass


class BlockingHTTPServer(HTTPServer):
    """Handle requests sequentially (no threading) to avoid GPU conflicts."""
    allow_reuse_address = True


if __name__ == '__main__':
    server = BlockingHTTPServer(('0.0.0.0', 7861), Handler)
    logger.info(f" Listening on :7861")
    server.serve_forever()
