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
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse

# Default model paths (match docker-compose volumes)
DEFAULT_DIFFUSION_MODEL = '/app/models/diffusion_models/z_image_turbo-Q8_0.gguf'
DEFAULT_VAE = '/app/models/vae/ae.safetensors'
DEFAULT_LLM = '/app/models/text_encoders/Qwen3-4B-Instruct-2507-Q4_K_M.gguf'
SD_CLI = '/usr/local/bin/sd-cli'

# Lock to serialize generation (one at a time)
_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[sd-wrapper] {format % args}")

    def handle(self):
        try:
            super().handle()
        except BrokenPipeError:
            pass  # Client disconnected, ignore

    def do_POST(self):
        path = urlparse(self.path).path
        if path != '/v1/images/generations':
            self.send_error(404, 'Not found')
            return

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
    steps = int(data.get('steps', 10))
    width = int(data.get('width', 1024))
    height = int(data.get('height', 1024))
    cfg_scale = float(data.get('cfg_scale', 1.0))
    seed = int(data.get('seed', -1))
    flow_shift = float(data.get('flow_shift', 2.0))

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

    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
        output_path = tmp.name

    cmd.extend(['-o', output_path])

    print(f"[sd-wrapper] Running: {' '.join(cmd[:12])}...")

    try:
        with open('/tmp/sd_cli_output.log', 'a') as log_file:
            result = subprocess.run(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                timeout=300
            )
        if result.returncode != 0:
            # Read last 500 chars of log for error details
            try:
                with open('/tmp/sd_cli_output.log', 'r') as f:
                    f.seek(max(0, f.tell() - 50000))
                    log_tail = f.read()[-500:]
            except Exception:
                log_tail = '(no log available)'
            return {'error': f'sd-cli failed (rc={result.returncode}): {log_tail}'}

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            return {'error': 'sd-cli produced empty output'}

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


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in separate threads."""
    daemon_threads = True


if __name__ == '__main__':
    server = ThreadedHTTPServer(('0.0.0.0', 7861), Handler)
    print(f"[sd-wrapper] Listening on :7861")
    server.serve_forever()
