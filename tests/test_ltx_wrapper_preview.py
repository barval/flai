"""LTX-Video wrapper poster-frame test.

The task checkpoint verifier is a text-only reasoning call and the web
container has no ffmpeg, so a generated video cannot be described there. The
wrapper keeps a representative frame in memory (the decoded frames already
exist) and returns it as ``preview_data``; that frame is what the executor
describes for the verifier.

The wrapper needs torch and runs inside its own container, so its tests read
the source as text (same convention as test_ltx_wrapper_offline.py).
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WRAPPER = REPO / "services" / "ltx_video" / "ltx_wrapper.py"
SOURCE = WRAPPER.read_text(encoding="utf-8")


def test_run_inference_returns_a_poster_frame():
    """The middle frame of the decoded clip is JPEG-encoded and returned."""
    start = SOURCE.index("video_np = images[0].permute")
    end = SOURCE.index("return mp4_bytes", start)
    block = SOURCE[start:end]
    assert "preview_data" in block, "the wrapper must build the poster frame next to the mp4"
    assert "JPEG" in block, "the poster frame must be JPEG-encoded (the multimodal model takes an image)"
    assert "base64.b64encode" in block, "the frame travels base64-encoded in the JSON response"


def test_route_ships_the_poster_frame():
    """The route puts the poster frame into the response JSON."""
    start = SOURCE.index('"file_name": filename')
    end = SOURCE.index("except ValueError", start)
    block = SOURCE[start:end]
    assert '"preview_data": meta.get("preview_data"' in block, "the poster frame must reach the caller"
