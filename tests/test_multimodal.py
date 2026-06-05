# tests/test_multimodal.py
"""Tests for multimodal module."""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from modules.multimodal import MultimodalModule


class TestMultimodalModule:
    """Test cases for MultimodalModule class."""

    @pytest.fixture
    def mock_app(self):
        """Create mock Flask app."""
        app = MagicMock()
        app.config = {
            "MAX_IMAGE_WIDTH": 3840,
            "MAX_IMAGE_HEIGHT": 2160,
            "MAX_IMAGE_SIZE_MB": 5,
            "TOKEN_CHARS": 3,
            "CONTEXT_HISTORY_PERCENT": 75,
            "SD_MODEL_TYPE": "z_image_turbo",
        }
        return app

    @pytest.fixture
    def multimodal_module(self, mock_app):
        """Create MultimodalModule with mock app."""
        with patch("modules.multimodal.LlamaCppClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.available = True
            mock_client_class.return_value = mock_client

            module = MultimodalModule(mock_app)
            return module

    def test_initialization(self, mock_app):
        """Should initialize with app."""
        with patch("modules.multimodal.LlamaCppClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.available = True
            mock_client_class.return_value = mock_client

            module = MultimodalModule(mock_app)
            assert module.available is True

    def test_image_settings_loaded(self, multimodal_module, mock_app):
        """Should load image settings from app config."""
        assert multimodal_module.image_settings["max_width"] == 3840
        assert multimodal_module.image_settings["max_height"] == 2160
        assert multimodal_module.image_settings["max_size_mb"] == 5

    def test_validate_image_valid(self, multimodal_module):
        """Should validate valid image."""
        with patch("modules.multimodal.Image.open") as mock_open:
            mock_img = MagicMock()
            mock_img.size = (800, 600)
            mock_img.format = "JPEG"
            mock_open.return_value = mock_img
            valid_jpg = base64.b64encode(b"fake image data").decode()
            is_valid, error = multimodal_module.validate_image(valid_jpg, "image/jpeg", "test.jpg", 1024)
            assert is_valid is True
            assert error is None

    def test_validate_image_too_large(self, multimodal_module):
        """Should reject too large image."""
        large_image = b"x" * (6 * 1024 * 1024)
        encoded = base64.b64encode(large_image).decode()
        is_valid, error = multimodal_module.validate_image(encoded, "image/jpeg", "test.jpg", 6 * 1024 * 1024)
        assert is_valid is False
        assert "5 MB" in error

    def test_validate_image_unsupported_type(self, multimodal_module):
        """Should reject unsupported image type."""
        valid_jpg = base64.b64encode(b"fake").decode()
        is_valid, error = multimodal_module.validate_image(valid_jpg, "image/gif", "test.gif", 1024)
        assert is_valid is False


class TestGenerateImageParams:
    """Test image parameter generation."""

    @pytest.fixture
    def module_with_mock_llamacpp(self):
        """Create module with mocked llama.cpp client."""
        with patch("modules.multimodal.LlamaCppClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.available = True
            mock_client.chat.return_value = "test response"
            mock_client_class.return_value = mock_client

            app = MagicMock()
            app.config = {
                "MAX_IMAGE_WIDTH": 3840,
                "MAX_IMAGE_HEIGHT": 2160,
                "MAX_IMAGE_SIZE_MB": 5,
                "TOKEN_CHARS": 3,
                "CONTEXT_HISTORY_PERCENT": 75,
                "SD_MODEL_TYPE": "z_image_turbo",
            }

            module = MultimodalModule(app)
            return module

    def test_generate_image_params_unavailable(self):
        """Should return error when model unavailable."""
        with patch("modules.multimodal.LlamaCppClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.available = False
            mock_client.check_availability.return_value = False
            mock_client_class.return_value = mock_client

            app = MagicMock()
            app.config = {"MAX_IMAGE_SIZE_MB": 5}

            module = MultimodalModule(app)
            result, error = module.generate_image_params("draw a cat")

            assert result is None
            assert "unavailable" in error.lower()

    def test_generate_image_params_parses_json(self, module_with_mock_llamacpp):
        """Should parse JSON response from model."""
        json_response = json.dumps(
            {
                "prompt": "a cat",
                "steps": 10,
                "width": 1024,
                "height": 1024,
                "cfg_scale": 1.0,
            }
        )
        module_with_mock_llamacpp.llamacpp.chat.return_value = json_response

        result, error = module_with_mock_llamacpp.generate_image_params("draw a cat")

        assert result is not None
        assert "prompt" in result
        assert result["prompt"] == "a cat"

    def test_generate_image_params_with_fallback_prompt(self, module_with_mock_llamacpp):
        """Should use original query if prompt missing in response."""
        json_response = json.dumps(
            {
                "steps": 10,
                "width": 1024,
                "height": 1024,
            }
        )
        module_with_mock_llamacpp.llamacpp.chat.return_value = json_response

        result, error = module_with_mock_llamacpp.generate_image_params("draw a cat")

        assert result is not None
        assert "prompt" in result

    def test_generate_image_params_adds_negative_prompt(self, module_with_mock_llamacpp):
        """Should add empty negative_prompt if missing."""
        json_response = json.dumps(
            {
                "prompt": "a cat",
                "steps": 10,
            }
        )
        module_with_mock_llamacpp.llamacpp.chat.return_value = json_response

        result, error = module_with_mock_llamacpp.generate_image_params("draw a cat")

        assert result["negative_prompt"] == ""


class TestCheckAvailability:
    """Test availability check."""

    def test_check_availability_delegates(self):
        """Should delegate to llama.cpp client."""
        with patch("modules.multimodal.LlamaCppClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.check_availability.return_value = True
            mock_client_class.return_value = mock_client

            app = MagicMock()
            app.config = {"MAX_IMAGE_SIZE_MB": 5}

            module = MultimodalModule(app)
            assert module.check_availability() is True


class TestGenerateVideoParams:
    """Test video parameter generation."""

    @pytest.fixture
    def module_with_mock_llamacpp(self):
        """Create module with mocked llama.cpp client."""
        with patch("modules.multimodal.LlamaCppClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.available = True
            mock_client.chat.return_value = "test response"
            mock_client_class.return_value = mock_client

            app = MagicMock()
            app.config = {
                "MAX_IMAGE_WIDTH": 3840,
                "MAX_IMAGE_HEIGHT": 2160,
                "MAX_IMAGE_SIZE_MB": 5,
                "TOKEN_CHARS": 3,
                "CONTEXT_HISTORY_PERCENT": 75,
                "SD_MODEL_TYPE": "z_image_turbo",
            }

            module = MultimodalModule(app)
            return module

    def test_generate_video_params_unavailable(self):
        """Should return error when model unavailable."""
        with patch("modules.multimodal.LlamaCppClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.available = False
            mock_client.check_availability.return_value = False
            mock_client_class.return_value = mock_client

            app = MagicMock()
            app.config = {"MAX_IMAGE_SIZE_MB": 5}

            module = MultimodalModule(app)
            result, error = module.generate_video_params("make a video of a cat")
            assert result is None
            assert "unavailable" in error.lower()

    def test_generate_video_params_parses_json(self, module_with_mock_llamacpp):
        """Should parse JSON response for video params."""
        import json

        json_response = json.dumps(
            {
                "prompt": "A cat walking on a beach, waves crashing",
                "width": 1216,
                "height": 704,
                "num_frames": 121,
                "frame_rate": 30,
            }
        )
        module_with_mock_llamacpp.llamacpp.chat.return_value = json_response

        result, error = module_with_mock_llamacpp.generate_video_params("make a video of a cat")
        assert result is not None
        assert result["prompt"] == "A cat walking on a beach, waves crashing"
        assert result["width"] == 1216
        assert result["height"] == 704
        assert result["num_frames"] == 121

    def test_generate_video_params_missing_defaults(self, module_with_mock_llamacpp):
        """Should fill defaults for missing fields."""
        import json

        json_response = json.dumps({"prompt": "A cat walking"})
        module_with_mock_llamacpp.llamacpp.chat.return_value = json_response

        result, error = module_with_mock_llamacpp.generate_video_params("make a video")
        assert result is not None
        assert result["width"] == 896
        assert result["height"] == 512
        assert result["num_frames"] == 257
        assert result["frame_rate"] == 30
        assert "negative_prompt" in result

    def test_generate_video_params_from_image(self, module_with_mock_llamacpp):
        """Should handle video params from image."""
        import json

        json_response = json.dumps(
            {
                "prompt": "The mountain landscape from the image, clouds moving",
                "width": 1216,
                "height": 704,
                "num_frames": 121,
            }
        )
        module_with_mock_llamacpp.llamacpp.chat_with_image.return_value = json_response

        fake_image = base64.b64encode(b"fake image").decode()
        result, error = module_with_mock_llamacpp.generate_video_params_from_image("animate this", fake_image)
        assert result is not None
        assert "mountain" in result["prompt"]


class TestValidateImageEdgeCases:
    """Test edge cases for image validation."""

    @pytest.fixture
    def module(self):
        """Create module for testing."""
        with patch("modules.multimodal.LlamaCppClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.available = True
            mock_client_class.return_value = mock_client

            app = MagicMock()
            app.config = {
                "MAX_IMAGE_WIDTH": 3840,
                "MAX_IMAGE_HEIGHT": 2160,
                "MAX_IMAGE_SIZE_MB": 5,
                "TOKEN_CHARS": 3,
                "CONTEXT_HISTORY_PERCENT": 75,
            }

            return MultimodalModule(app)

    def test_validate_corrupt_image(self, module):
        """Should handle corrupt image data."""
        is_valid, error = module.validate_image("not-valid-base64!!!", "image/jpeg", "test.jpg", 1024)
        assert is_valid is False

    def test_validate_exactly_max_size(self, module):
        """Should accept image at exactly max size."""
        max_size = 5 * 1024 * 1024
        data = b"x" * max_size
        encoded = base64.b64encode(data).decode()

        with patch("modules.multimodal.Image.open") as mock_open:
            mock_img = MagicMock()
            mock_img.size = (800, 600)
            mock_img.format = "JPEG"
            mock_open.return_value = mock_img
            is_valid, error = module.validate_image(encoded, "image/jpeg", "test.jpg", max_size)
            assert is_valid is True

    def test_validate_just_over_max_size(self, module):
        """Should reject image just over max size."""
        max_size = 5 * 1024 * 1024 + 1
        data = b"x" * max_size
        encoded = base64.b64encode(data).decode()

        is_valid, error = module.validate_image(encoded, "image/jpeg", "test.jpg", max_size)
        assert is_valid is False


class TestValidateImageFormat:
    """Test format compatibility check against llama.cpp stb_image."""

    @pytest.fixture
    def module(self):
        with patch("modules.multimodal.LlamaCppClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.available = True
            mock_client_class.return_value = mock_client
            app = MagicMock()
            app.config = {
                "MAX_IMAGE_WIDTH": 3840,
                "MAX_IMAGE_HEIGHT": 2160,
                "MAX_IMAGE_SIZE_MB": 5,
                "TOKEN_CHARS": 3,
                "CONTEXT_HISTORY_PERCENT": 75,
            }
            return MultimodalModule(app)

    @staticmethod
    def _patch_image_open(mock_open, fmt="JPEG", size=(100, 100)):
        mock_img = MagicMock()
        mock_img.size = size
        mock_img.format = fmt
        mock_open.return_value = mock_img

    def test_validate_jpeg_accepted(self, module):
        with patch("modules.multimodal.Image.open") as mock_open:
            self._patch_image_open(mock_open, fmt="JPEG")
            encoded = base64.b64encode(b"fake-jpeg").decode()
            is_valid, error = module.validate_image(encoded, "image/jpeg", "test.jpg", 1024)
            assert is_valid is True
            assert error is None

    def test_validate_png_accepted(self, module):
        with patch("modules.multimodal.Image.open") as mock_open:
            self._patch_image_open(mock_open, fmt="PNG")
            encoded = base64.b64encode(b"fake-png").decode()
            is_valid, error = module.validate_image(encoded, "image/png", "test.png", 1024)
            assert is_valid is True
            assert error is None

    def test_validate_gif_accepted(self, module):
        with patch("modules.multimodal.Image.open") as mock_open:
            self._patch_image_open(mock_open, fmt="GIF")
            encoded = base64.b64encode(b"fake-gif").decode()
            is_valid, error = module.validate_image(encoded, "image/gif", "test.gif", 1024)
            assert is_valid is True
            assert error is None

    def test_validate_heic_rejected(self, module):
        """HEIC images pass Pillow but fail llama.cpp's stb_image decoder."""
        # Bypass the TranslationMixin in this test so the placeholder is rendered
        with patch.object(
            module,
            "_",
            side_effect=lambda key, lang="ru", **kw: key.format(**kw) if kw else key,
        ), patch("modules.multimodal.Image.open") as mock_open:
            self._patch_image_open(mock_open, fmt="HEIC")
            encoded = base64.b64encode(b"fake-heic").decode()
            is_valid, error = module.validate_image(encoded, "image/heic", "test.heic", 1024)
            assert is_valid is False
            assert "HEIC" in error

    def test_validate_avif_rejected(self, module):
        """AVIF images pass Pillow but fail llama.cpp's stb_image decoder."""
        with patch.object(
            module,
            "_",
            side_effect=lambda key, lang="ru", **kw: key.format(**kw) if kw else key,
        ), patch("modules.multimodal.Image.open") as mock_open:
            self._patch_image_open(mock_open, fmt="AVIF")
            encoded = base64.b64encode(b"fake-avif").decode()
            is_valid, error = module.validate_image(encoded, "image/avif", "test.avif", 1024)
            assert is_valid is False
            assert "AVIF" in error


class TestImageAutoConvert:
    """Test _ensure_llamacpp_compatible hook in MultimodalModule."""

    @pytest.fixture
    def module(self):
        with patch("modules.multimodal.LlamaCppClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.available = True
            mock_client_class.return_value = mock_client
            app = MagicMock()
            app.config = {
                "MAX_IMAGE_WIDTH": 3840,
                "MAX_IMAGE_HEIGHT": 2160,
                "MAX_IMAGE_SIZE_MB": 5,
                "TOKEN_CHARS": 3,
                "CONTEXT_HISTORY_PERCENT": 75,
            }
            return MultimodalModule(app)

    def test_ensure_llamacpp_compatible_passthrough_for_jpeg(self, module):
        """JPEG input is returned unchanged (no conversion)."""
        with patch("app.utils.convert_to_supported_format_if_needed") as mock_convert:
            mock_convert.return_value = ("abc", "image/jpeg", "t.jpg", False)
            data, was_converted = module._ensure_llamacpp_compatible("abc")
            assert data == "abc"
            assert was_converted is False
            assert mock_convert.called

    def test_ensure_llamacpp_compatible_converts_webp(self, module):
        """WebP input is flagged as converted."""
        with patch("app.utils.convert_to_supported_format_if_needed") as mock_convert:
            mock_convert.return_value = ("xyz", "image/jpeg", "t.jpg", True)
            data, was_converted = module._ensure_llamacpp_compatible("xyz")
            assert data == "xyz"
            assert was_converted is True

    def test_ensure_llamacpp_compatible_handles_exception(self, module):
        """If convert raises, return original data unchanged."""
        with patch("app.utils.convert_to_supported_format_if_needed", side_effect=Exception("boom")):
            data, was_converted = module._ensure_llamacpp_compatible("original")
            assert data == "original"
            assert was_converted is False

    def test_chat_with_image_uses_converted_data(self, module):
        """process_image_with_text sends the converted data to llama.cpp client."""
        module.llamacpp.chat_with_image = MagicMock(return_value="ok")
        with patch("app.utils.convert_to_supported_format_if_needed") as mock_convert:
            mock_convert.return_value = ("converted_data", "image/jpeg", "t.jpg", True)
            module.process_image_with_text("describe this", "original", "multimodal", "en")
            # chat_with_image must be called with the converted payload
            assert module.llamacpp.chat_with_image.called
            kwargs = module.llamacpp.chat_with_image.call_args.kwargs
            assert "converted_data" in kwargs["image_base64"]
