"""Tests for token-estimation calibration and clean history formatting."""

from app.llamacpp_client import _record_prompt_tokens
from app.utils import (
    _TOKEN_CALIBRATION,
    TOKEN_COEFFICIENTS,
    build_context_prompt,
    calibrate_token_chars,
    estimate_tokens,
    record_token_calibration,
    reset_token_calibration,
)


def test_calibration_requires_minimum_samples():
    reset_token_calibration()
    try:
        assert calibrate_token_chars("multimodal", "ru") is None
        record_token_calibration("multimodal", "ru", chars=100, tokens=50)
        # Fewer than the minimum sample count -> fallback to None
        assert calibrate_token_chars("multimodal", "ru") is None
    finally:
        reset_token_calibration()


def test_calibration_median_coefficient():
    reset_token_calibration()
    try:
        # 200 chars / 100 tokens = 2.0; 200 chars / 50 tokens = 4.0
        record_token_calibration("multimodal", "ru", chars=200, tokens=100)
        record_token_calibration("multimodal", "ru", chars=200, tokens=50)
        record_token_calibration("multimodal", "ru", chars=200, tokens=80)
        # Median of [2.0, 2.5, 4.0] is 2.5
        assert calibrate_token_chars("multimodal", "ru") == 2.5
    finally:
        reset_token_calibration()


def test_estimate_tokens_uses_calibrated_coefficient():
    reset_token_calibration()
    try:
        base_coeff = TOKEN_COEFFICIENTS.get(("multimodal", "ru"), 3.0)
        assert estimate_tokens("привет мир", "multimodal", "ru") == int(len("привет мир") / base_coeff) + 1

        # 100 chars / 50 tokens = char-per-token factor 2.0
        record_token_calibration("multimodal", "ru", chars=100, tokens=50)
        record_token_calibration("multimodal", "ru", chars=100, tokens=50)
        record_token_calibration("multimodal", "ru", chars=100, tokens=50)
        assert estimate_tokens("привет мир", "multimodal", "ru") == int(len("привет мир") / 2.0) + 1
    finally:
        reset_token_calibration()


def test_calibration_ignores_invalid_samples():
    reset_token_calibration()
    try:
        record_token_calibration("multimodal", "ru", chars=0, tokens=100)
        record_token_calibration("multimodal", "ru", chars=100, tokens=0)
        assert calibrate_token_chars("multimodal", "ru") is None
    finally:
        reset_token_calibration()


def test_build_context_prompt_has_no_timestamps():
    history = [
        {"role": "user", "content": "Hello", "timestamp": "2026-09-13T18:45:55"},
        {"role": "assistant", "content": "Hi there", "timestamp": "2026-09-13T18:46:01"},
    ]
    prompt = build_context_prompt(history)
    assert "User: Hello" in prompt
    assert "Assistant: Hi there" in prompt
    assert "2026-" not in prompt
    assert "[" not in prompt or "18:45" not in prompt


def calibration_samples(model_type, lang):
    return _TOKEN_CALIBRATION.get((model_type, lang), [])


def test_build_context_prompt_empty():
    assert build_context_prompt([]) == ""


def test_record_prompt_tokens_stream_usage_dict():
    """Stream chunks carry the raw usage dict (no top-level 'usage' key)."""
    reset_token_calibration()
    try:
        _record_prompt_tokens(
            {"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55},
            "multimodal",
            "ru",
            [{"role": "user", "content": "Hello world"}],
        )
        # 1 sample < minimum -> still not calibrated, but recorded
        assert calibrate_token_chars("multimodal", "ru") is None
        assert len(calibration_samples("multimodal", "ru")) == 1
    finally:
        reset_token_calibration()


def test_record_prompt_tokens_full_response():
    """Non-stream responses carry a nested top-level 'usage' key."""
    reset_token_calibration()
    try:
        _record_prompt_tokens(
            {
                "choices": [{"message": {"content": ""}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 5, "total_tokens": 55},
            },
            "multimodal",
            "ru",
            [{"role": "user", "content": "Hello world"}],
        )
        assert len(calibration_samples("multimodal", "ru")) == 1
    finally:
        reset_token_calibration()
