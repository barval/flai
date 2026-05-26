#!/usr/bin/env python3
"""
HTTP wrapper for SuperLocalMemory CLI with per-user isolation.

Each user gets their own SQLite database via $HOME/.superlocalmemory/.
The embedding model is shared via HF_HOME pointing to the global cache.
No daemon needed — all calls use --sync for immediate processing.
"""

import json
import os
import subprocess
import sys

from flask import Flask, jsonify, request

SLM_DATA_DIR = "/app/data/slm"
SHARED_CACHE = "/root/.cache/huggingface"

app = Flask(__name__)


def _slm(args: list[str], profile: str | None = None) -> dict:
    """Run slm CLI with given args and optional per-user isolation."""
    env = os.environ.copy()
    env["HF_HOME"] = SHARED_CACHE
    if profile:
        home_dir = os.path.join(SLM_DATA_DIR, profile)
        os.makedirs(home_dir, exist_ok=True, mode=0o755)
        os.chmod(home_dir, 0o755)
        env["HOME"] = home_dir
    try:
        result = subprocess.run(
            ["slm"] + args,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        if result.returncode != 0:
            return {"success": False, "error": result.stderr.strip() or f"slm exited with code {result.returncode}"}
        if not result.stdout.strip():
            return {"success": True, "raw": ""}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"success": True, "raw": result.stdout.strip()}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "slm command timed out"}
    except FileNotFoundError:
        return {"success": False, "error": "slm command not found"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "superlocalmemory"})


@app.route("/remember", methods=["POST"])
def remember():
    data = request.get_json(force=True)
    text = data.get("text", "")
    if not text:
        return jsonify({"success": False, "error": "Missing text"}), 400
    profile = data.get("profile")
    result = _slm(["remember", text, "--json", "--sync"], profile=profile)
    return jsonify(result)


@app.route("/recall", methods=["POST"])
def recall():
    data = request.get_json(force=True)
    query = data.get("query", "")
    limit = data.get("limit", 5)
    if not query:
        return jsonify({"success": False, "error": "Missing query"}), 400
    profile = data.get("profile")
    result = _slm(["recall", query, "--limit", str(limit), "--json"], profile=profile)
    return jsonify(result)


@app.route("/forget", methods=["POST"])
def forget():
    data = request.get_json(force=True)
    query = data.get("query", "")
    if not query:
        return jsonify({"success": False, "error": "Missing query"}), 400
    profile = data.get("profile")
    result = _slm(["forget", query, "--json", "--yes"], profile=profile)
    return jsonify(result)


@app.route("/list", methods=["POST"])
def list_facts():
    data = request.get_json(force=True)
    limit = data.get("limit", 20)
    profile = data.get("profile")
    result = _slm(["list", "--json", "-n", str(limit)], profile=profile)
    return jsonify(result)


@app.route("/", methods=["GET"])
def root():
    return jsonify({"service": "superlocalmemory", "status": "running"})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
