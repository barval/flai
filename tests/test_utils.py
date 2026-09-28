# tests/test_utils.py
"""Unit tests for utility functions."""

import base64
import io

import pytest
from PIL import Image

from app.utils import (
    chunk_text,
    convert_to_supported_format_if_needed,
    extract_text_from_file,
    format_prompt,
    remove_gguf_cache_entries,
    resize_image_if_needed,
)


@pytest.mark.unit
def test_format_prompt(tmp_path):
    """Test prompt template formatting."""
    prompts_dir = tmp_path / "prompts" / "ru"
    prompts_dir.mkdir(parents=True)
    template_file = prompts_dir / "test.template"
    template_file.write_text("Hello, {name}!")

    # Patch the PROMPTS_DIR global for the test
    import app.utils

    original_dir = app.utils.PROMPTS_DIR
    app.utils.PROMPTS_DIR = str(tmp_path / "prompts")

    try:
        result = format_prompt("test.template", {"name": "World"}, lang="ru")
        assert result == "Hello, World!"
    finally:
        app.utils.PROMPTS_DIR = original_dir


class _FakeCursor:
    def __init__(self):
        self.executed_sql = None
        self.params = None

    def execute(self, sql, params=()):
        self.executed_sql = sql
        self.params = params


class _FakeConn:
    def __init__(self):
        self.cur = _FakeCursor()

    def cursor(self):
        return self.cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def commit(self):
        pass


@pytest.mark.unit
def test_remove_gguf_cache_entries_drops_memory_and_db(monkeypatch):
    """remove_gguf_cache_entries must drop the entry from the in-memory cache
    AND issue a DELETE against the gguf_models_cache table."""
    import app.utils as u
    from app import database

    u._gguf_models_cache = {"alpha": {"file_size_mb": 1}, "beta": {}, "gamma": {}}
    conn = _FakeConn()
    monkeypatch.setattr(database, "get_db", lambda: conn)
    try:
        remove_gguf_cache_entries(["alpha", "gamma"])
        assert u._gguf_models_cache == {"beta": {}}
    finally:
        u._gguf_models_cache = None
    assert "DELETE FROM gguf_models_cache" in conn.cur.executed_sql
    assert set(conn.cur.params) == {"alpha", "gamma"}


@pytest.mark.unit
def test_chunk_text():
    """Test text chunking by characters with overlap."""
    text = "Hello, this is a test text for chunking."
    chunks = chunk_text(text, chunk_size=10, overlap=3)
    assert len(chunks) > 0
    # First chunk contains first 10 characters
    assert chunks[0] == "Hello, thi"
    # Check overlap between consecutive chunks (in characters)
    if len(chunks) > 1:
        assert chunks[0][-3:] == chunks[1][:3]


@pytest.mark.unit
def test_resize_image_if_needed_small():
    """Test that image not resized if dimensions OK."""
    # Create small image
    img = Image.new("RGB", (100, 100), color="red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    img_data = base64.b64encode(buf.getvalue()).decode("utf-8")
    new_data, new_type, new_name, resized, orig_dims, new_dims = resize_image_if_needed(
        img_data, "image/jpeg", "test.jpg", 3840, 2160
    )
    assert not resized
    assert new_data == img_data


@pytest.mark.unit
def test_resize_image_if_needed_large():
    """Test that image is resized when too large."""
    # Create large image
    img = Image.new("RGB", (4000, 3000), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    img_data = base64.b64encode(buf.getvalue()).decode("utf-8")
    new_data, new_type, new_name, resized, orig_dims, new_dims = resize_image_if_needed(
        img_data, "image/jpeg", "test.jpg", 1080, 85
    )
    assert resized
    assert new_type == "image/jpeg"
    assert new_name.endswith(".jpg")
    # Decode and check new dimensions
    decoded = base64.b64decode(new_data)
    new_img = Image.open(io.BytesIO(decoded))
    assert new_img.width <= 1080
    assert new_img.height <= 1080


@pytest.mark.unit
def test_convert_to_supported_format_jpeg_passthrough():
    """JPEG input is returned as-is (no conversion needed)."""
    img = Image.new("RGB", (100, 100), color="red")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    img_data = base64.b64encode(buf.getvalue()).decode("utf-8")
    new_data, new_type, new_name, converted = convert_to_supported_format_if_needed(img_data, "image/jpeg", "test.jpg")
    assert not converted
    assert new_data == img_data
    assert new_type == "image/jpeg"
    assert new_name == "test.jpg"


@pytest.mark.unit
def test_convert_to_supported_format_webp_to_jpeg():
    """WebP image is converted to JPEG for llama.cpp compatibility."""
    img = Image.new("RGB", (100, 100), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    img_data = base64.b64encode(buf.getvalue()).decode("utf-8")
    new_data, new_type, new_name, converted = convert_to_supported_format_if_needed(img_data, "image/webp", "test.webp")
    assert converted
    assert new_type == "image/jpeg"
    assert new_name.endswith(".jpg")
    # Decode and verify
    decoded = base64.b64decode(new_data)
    new_img = Image.open(io.BytesIO(decoded))
    assert new_img.format == "JPEG"
    assert new_img.mode == "RGB"


@pytest.mark.unit
def test_convert_to_supported_format_rgba_to_jpeg():
    """RGBA PNG is converted to RGB JPEG (no alpha)."""
    img = Image.new("RGBA", (50, 50), (255, 0, 0, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_data = base64.b64encode(buf.getvalue()).decode("utf-8")
    new_data, new_type, new_name, converted = convert_to_supported_format_if_needed(img_data, "image/png", "test.png")
    # PNG is already in the supported set, so no conversion needed
    assert not converted
    assert new_data == img_data


@pytest.mark.unit
def test_convert_to_supported_format_strips_data_uri():
    """data:image/png;base64,... prefix is stripped before decoding."""
    img = Image.new("RGBA", (10, 10), (0, 255, 0, 200))
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    raw = base64.b64encode(buf.getvalue()).decode("utf-8")
    data_uri = f"data:image/webp;base64,{raw}"
    new_data, new_type, new_name, converted = convert_to_supported_format_if_needed(data_uri, "image/webp", "test.webp")
    assert converted
    assert new_type == "image/jpeg"


@pytest.mark.unit
def test_convert_to_supported_format_invalid_data_returns_original():
    """Invalid base64 / garbage data falls back to original (no crash)."""
    new_data, new_type, new_name, converted = convert_to_supported_format_if_needed(
        "not-valid-base64!!!", "image/jpeg", "broken.jpg"
    )
    assert not converted
    assert new_data == "not-valid-base64!!!"
    assert new_type == "image/jpeg"


@pytest.mark.unit
def test_extract_text_from_file(tmp_path):
    """Test text extraction from .txt file."""
    file = tmp_path / "test.txt"
    content = "Hello world\nSecond line"
    file.write_text(content, encoding="utf-8")
    extracted = extract_text_from_file(str(file))
    assert extracted == content
