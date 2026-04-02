# tests/test_utils.py
"""Unit tests for utility functions."""
import pytest
from app.utils import (
    format_prompt, load_prompt_template, chunk_text,
    get_current_time_in_timezone, get_current_time_in_timezone_for_db,
    resize_image_if_needed, extract_text_from_file
)
import os
import tempfile
import base64
from PIL import Image
import io


@pytest.mark.unit
def test_format_prompt(tmp_path):
    """Test prompt template formatting."""
    prompts_dir = tmp_path / 'prompts' / 'ru'
    prompts_dir.mkdir(parents=True)
    template_file = prompts_dir / 'test.template'
    template_file.write_text("Hello, {name}!")

    # Patch the PROMPTS_DIR global for the test
    import app.utils
    original_dir = app.utils.PROMPTS_DIR
    app.utils.PROMPTS_DIR = str(tmp_path / 'prompts')

    try:
        result = format_prompt('test.template', {'name': 'World'}, lang='ru')
        assert result == "Hello, World!"
    finally:
        app.utils.PROMPTS_DIR = original_dir


@pytest.mark.unit
def test_chunk_text():
    """Test text chunking with overlap."""
    text = " ".join([f"word{i}" for i in range(100)])
    chunks = chunk_text(text, chunk_size=10, overlap=2)
    assert len(chunks) > 0
    # Check that first chunk contains first 10 words
    assert chunks[0].startswith("word0")
    # Check overlap between consecutive chunks
    if len(chunks) > 1:
        first_words_chunk1 = chunks[0].split()[-2:]
        first_words_chunk2 = chunks[1].split()[:2]
        assert first_words_chunk1 == first_words_chunk2


@pytest.mark.unit
def test_resize_image_if_needed_small():
    """Test that image not resized if dimensions OK."""
    # Create small image
    img = Image.new('RGB', (100, 100), color='red')
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    img_data = base64.b64encode(buf.getvalue()).decode('utf-8')
    new_data, new_type, new_name, resized, orig_dims, new_dims = resize_image_if_needed(
        img_data, 'image/jpeg', 'test.jpg', 3840, 2160
    )
    assert not resized
    assert new_data == img_data


@pytest.mark.unit
def test_resize_image_if_needed_large():
    """Test that image is resized when too large."""
    # Create large image
    img = Image.new('RGB', (4000, 3000), color='blue')
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    img_data = base64.b64encode(buf.getvalue()).decode('utf-8')
    new_data, new_type, new_name, resized, orig_dims, new_dims = resize_image_if_needed(
        img_data, 'image/jpeg', 'test.jpg', 1920, 1080
    )
    assert resized
    assert new_type == 'image/jpeg'
    assert new_name.endswith('.jpg')
    # Decode and check new dimensions
    decoded = base64.b64decode(new_data)
    new_img = Image.open(io.BytesIO(decoded))
    assert new_img.width <= 1920
    assert new_img.height <= 1080


@pytest.mark.unit
def test_extract_text_from_file(tmp_path):
    """Test text extraction from .txt file."""
    file = tmp_path / 'test.txt'
    content = "Hello world\nSecond line"
    file.write_text(content, encoding='utf-8')
    extracted = extract_text_from_file(str(file))
    assert extracted == content
