#!/usr/bin/env python3
"""
HTTP proxy for SuperLocalMemory daemon.

Forwards requests to the local SLM daemon (slm serve, port 8765)
which keeps the MemoryEngine and embedding model in memory.
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

from flask import Flask, jsonify, request

DAEMON_URL = "http://localhost:8765"

app = Flask(__name__)


def _daemon_get(path: str) -> dict:
    """GET request to daemon."""
    try:
        resp = urllib.request.urlopen(f"{DAEMON_URL}{path}", timeout=30)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"daemon HTTP {e.code}: {e.read().decode()}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _daemon_post(path: str, body: dict) -> dict:
    """POST request to daemon."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{DAEMON_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=300)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"daemon HTTP {e.code}: {e.read().decode()}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.route("/health")
def health():
    """Health check — delegate to daemon."""
    try:
        resp = urllib.request.urlopen(f"{DAEMON_URL}/health", timeout=5)
        data = json.loads(resp.read().decode())
        return jsonify({"status": "ok", "service": "superlocalmemory", "daemon": data.get("status")})
    except Exception:
        return jsonify({"status": "ok", "service": "superlocalmemory", "daemon": "unreachable"})


@app.route("/remember", methods=["POST"])
def remember():
    """Store a fact — forward to daemon."""
    data = request.get_json(force=True)
    text = data.get("text", "")
    if not text:
        return jsonify({"success": False, "error": "Missing text"}), 400

    meta = data.get("metadata", {})
    if data.get("profile"):
        meta["profile"] = data["profile"]

    result = _daemon_post("/remember?wait=true", {
        "content": text,
        "tags": "",
        "metadata": meta,
    })
    return jsonify({
        "success": result.get("ok", False),
        "fact_ids": result.get("fact_ids", []),
        "error": result.get("error", ""),
    })


@app.route("/recall", methods=["POST"])
def recall():
    """Retrieve relevant facts — forward to daemon."""
    data = request.get_json(force=True)
    query = data.get("query", "")
    limit = data.get("limit", 5)
    if not query:
        return jsonify({"success": False, "error": "Missing query"}), 400

    result = _daemon_get(f"/recall?q={urllib.parse.quote(query)}&limit={limit}&fast=true")
    results = []
    for r in result.get("results", []):
        results.append({
            "content": r.get("content", ""),
            "score": r.get("score", 0),
            "confidence": r.get("confidence", 0),
            "fact_id": r.get("fact_id", ""),
        })
    return jsonify({
        "success": result.get("ok", False),
        "data": {"results": results},
        "error": result.get("error", ""),
    })


@app.route("/forget", methods=["POST"])
def forget():
    """Not supported via daemon — return success (no-op)."""
    return jsonify({"success": True, "note": "forget not supported via daemon"})


@app.route("/list", methods=["POST"])
def list_facts():
    """Not supported via daemon — return empty."""
    return jsonify({"success": True, "data": {"results": []}})


@app.route("/", methods=["GET"])
def root():
    return jsonify({"service": "superlocalmemory", "daemon_proxy": True})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
