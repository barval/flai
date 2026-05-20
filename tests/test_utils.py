# tests/test_utils.py
"""Unit tests for utility functions."""

import base64
import io

import pytest
from PIL import Image

from app.utils import (
    chunk_text,
    extract_text_from_file,
    format_prompt,
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
        img_data, "image/jpeg", "test.jpg", 1920, 1080
    )
    assert resized
    assert new_type == "image/jpeg"
    assert new_name.endswith(".jpg")
    # Decode and check new dimensions
    decoded = base64.b64decode(new_data)
    new_img = Image.open(io.BytesIO(decoded))
    assert new_img.width <= 1920
    assert new_img.height <= 1080


@pytest.mark.unit
def test_extract_text_from_file(tmp_path):
    """Test text extraction from .txt file."""
    file = tmp_path / "test.txt"
    content = "Hello world\nSecond line"
    file.write_text(content, encoding="utf-8")
    extracted = extract_text_from_file(str(file))
    assert extracted == content
