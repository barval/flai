# modules/multimodal.py
import base64
import json
import logging
import os
from collections.abc import Generator
from io import BytesIO
from typing import Any

from PIL import Image

from app.db import get_session_text_history
from app.llamacpp_client import LlamaCppClient
from app.mixins import TranslationMixin
from app.utils import build_context_prompt, estimate_tokens, format_prompt


class MultimodalModule(TranslationMixin):
    """Module for multimodal model (image processing)."""

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.llamacpp = LlamaCppClient(app)
        self.available = self.llamacpp.available
        self.image_settings = {}
        self.token_chars = 3
        self.context_history_percent = 75
        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize module with Flask app."""
        self.app = app
        self.llamacpp.init_app(app)
        self.available = self.llamacpp.available

        self.image_settings = {
            "max_width": app.config.get("MAX_IMAGE_WIDTH", 3840),
            "max_height": app.config.get("MAX_IMAGE_HEIGHT", 2160),
            "max_size_mb": app.config.get("MAX_IMAGE_SIZE_MB", 5),
            "max_size_bytes": app.config.get("MAX_IMAGE_SIZE_MB", 5) * 1024 * 1024,
            "supported_mimetypes": {
                "image/jpeg",
                "image/jpg",
                "image/jpe",
                "image/png",
                "image/bmp",
                "image/x-ms-bmp",
                "image/webp",
                "image/tiff",
                "image/tif",
            },
            "supported_extensions": {".jpg", ".jpeg", ".jpe", ".png", ".bmp", ".webp", ".tif", ".tiff"},
        }

        self.token_chars = app.config.get("TOKEN_CHARS", 3)
        self.context_history_percent = app.config.get("CONTEXT_HISTORY_PERCENT", 75)

        if self.available:
            self.logger.info("MultimodalModule initialized and available.")
        else:
            self.logger.warning("MultimodalModule initialized, but multimodal model unavailable")

    def _get_model_config(self) -> dict[str, Any] | None:
        """Retrieve multimodal model configuration."""
        from app.model_config import get_model_config

        return get_model_config("multimodal")  # type: ignore[no-any-return]

    def validate_image(
        self, file_data: str, file_type: str, file_name: str, file_size: int, lang: str = "ru"
    ) -> tuple[bool, str | None]:
        """Validate image against requirements."""
        if file_size > self.image_settings["max_size_bytes"]:
            template = self._("Maximum file size {max_size} MB", lang)
            return False, template.format(max_size=self.image_settings["max_size_mb"])

        if file_type not in self.image_settings["supported_mimetypes"]:
            ext = os.path.splitext(file_name)[1].lower()
            if ext not in self.image_settings["supported_extensions"]:
                return False, self._("Unsupported file type", lang)

        try:
            image_bytes = base64.b64decode(file_data)
            img = Image.open(BytesIO(image_bytes))
            width, height = img.size

            if width > self.image_settings["max_width"] or height > self.image_settings["max_height"]:
                template = self._("Maximum resolution {max_width}x{max_height}", lang)
                return False, template.format(
                    max_width=self.image_settings["max_width"], max_height=self.image_settings["max_height"]
                )

            return True, None
        except Exception as e:
            self.logger.error(f"Error validating image: {str(e)}")
            return False, self._("Could not process image file", lang)

    # --- Context handling ---
    def _estimate_tokens(self, text: str) -> int:
        return estimate_tokens(text, self.token_chars)

    def _build_context_prompt(self, history: list[dict[str, str]], lang: str = "ru") -> str:
        return build_context_prompt(history, lang)

    def _get_context_for_model(self, session_id: str, current_query: str, lang: str = "ru") -> str:
        """Retrieve text-only history for multimodal model."""
        if not session_id:
            return ""

        model_config = self._get_model_config()
        if not model_config:
            return ""
        max_context_tokens = model_config.get("context_length", 32768)
        available_tokens = int(max_context_tokens * (self.context_history_percent / 100.0))

        overhead = 500
        query_tokens = self._estimate_tokens(current_query)
        remaining_for_history = available_tokens - query_tokens - overhead
        if remaining_for_history <= 0:
            return ""

        history_msgs = get_session_text_history(session_id, remaining_for_history)
        return self._build_context_prompt(history_msgs, lang)

    def _prepare_image_prompt(
        self,
        user_text: str,
        current_time_str: str,
        lang: str,
        session_id: str | None,
        response_style: str,
    ) -> str | None:
        """Build the prompt text for image processing. Returns None on error."""
        response_language = "Russian" if lang == "ru" else "English"
        style_map = {
            "ru": {
                "neutral": "Без особого стиля.",
                "academic": "Отвечай в формальном академическом стиле. Используй точную терминологию, строгие формулировки и логически структурированные аргументы. Избегай разговорных выражений. При необходимости ссылайся на факты.",
                "professional": "Отвечай в профессиональном деловом стиле. Будь чётким, конкретным и по делу. Используй ясные формулировки. Избегай лишних эмоций и воды.",
                "friendly": "Отвечай в тёплом дружеском стиле. Будь приветлив и располагай к общению. Используй естественный разговорный тон. Покажи эмпатию и заботу о пользователе. Можно использовать эмодзи, если они уместны и помогают выразить эмоцию.",
                "funny": "Отвечай с юмором и остроумием. Будь игрив и занимателен. Используй шутки, метафоры и неожиданные сравнения, но не забывай давать полезную информацию по существу вопроса. Эмодзи приветствуются, если они к месту и усиливают эффект.",
            },
            "en": {
                "neutral": "Default style.",
                "academic": "Answer in a formal academic style. Use precise terminology, rigorous wording, and logically structured arguments. Avoid colloquial expressions. Reference facts where appropriate.",
                "professional": "Answer in a professional business-like style. Be clear, specific, and to the point. Use straightforward wording. Avoid unnecessary emotions or fluff.",
                "friendly": "Answer in a warm, friendly style. Be welcoming and approachable. Use a natural conversational tone. Show empathy and care for the user. You may use emojis when they are appropriate and help convey emotion.",
                "funny": "Answer with humor and wit. Be playful and entertaining. Use jokes, metaphors, and unexpected comparisons, but still provide useful information on the topic. Emojis are welcome when they fit the context and enhance the effect.",
            },
        }
        style_instruction = style_map.get(lang, style_map["ru"]).get(response_style, style_map["ru"]["neutral"])

        context_str = self._get_context_for_model(session_id, user_text, lang)  # type: ignore[arg-type]

        if user_text.strip():
            prompt = format_prompt(
                "image_text.template",
                {
                    "current_time_str": current_time_str,
                    "user_query": user_text,
                    "response_language": response_language,
                    "conversation_history": context_str,
                    "response_style": style_instruction,
                },
                lang=lang,
            )
        else:
            prompt = format_prompt(
                "image.template",
                {
                    "current_time_str": current_time_str,
                    "response_language": response_language,
                    "conversation_history": context_str,
                    "response_style": style_instruction,
                },
                lang=lang,
            )

        if not prompt:
            self.logger.error("Failed to load image prompt template")
        return prompt

    def process_image_with_text(
        self,
        image_data: str,
        user_text: str,
        current_time_str: str,
        lang: str = "ru",
        session_id: str | None = None,
        response_style: str = "neutral",
    ) -> tuple[str | None, str | None]:
        """Process image with text, including conversation history."""
        if not self.check_availability():
            return None, self._("Multimodal model unavailable", lang)

        prompt = self._prepare_image_prompt(user_text, current_time_str, lang, session_id, response_style)
        if not prompt:
            return None, self._("Error loading prompt template", lang)

        response = self.llamacpp.chat_with_image(
            text=prompt, image_base64=image_data, model_type="multimodal", lang=lang
        )
        return response, None

    def process_image_with_text_stream(
        self,
        image_data: str,
        user_text: str,
        current_time_str: str,
        lang: str = "ru",
        session_id: str | None = None,
        response_style: str = "neutral",
    ) -> Generator[str, None, None]:
        """Stream multimodal response for image+text, token by token."""
        if not self.check_availability():
            yield "⚠️ " + self._("Multimodal model unavailable", lang)
            return

        prompt = self._prepare_image_prompt(user_text, current_time_str, lang, session_id, response_style)
        if not prompt:
            yield "⚠️ " + self._("Error loading prompt template", lang)
            return

        yield from self.llamacpp.chat_with_image_stream(
            text=prompt, image_base64=image_data, model_type="multimodal", lang=lang
        )

    def generate_image_params(
        self, user_query: str, lang: str = "ru", response_style: str = "neutral"
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Generate parameters for image creation.
        Chooses the prompt template based on SD_MODEL_TYPE config.
        """
        if not self.check_availability():
            return None, self._("Multimodal model unavailable", lang)

        # Select template based on SD_MODEL_TYPE
        sd_model_type = self.app.config.get("SD_MODEL_TYPE", "z_image_turbo")
        template_name = f"create_image_{sd_model_type}.template"

        create_prompt = format_prompt(
            template_name,
            {
                "image_query": user_query,
                "response_style": "",
            },
            lang=lang,
        )

        if not create_prompt:
            return None, self._("Error loading prompt template", lang)

        messages = [
            {
                "role": "system",
                "content": "You are an image generation parameter generator. Always respond with valid JSON only, no explanations.",
            },
            {"role": "user", "content": create_prompt},
        ]

        response = self._call_multimodal(messages, lang=lang)

        self.logger.info(f"Multimodal model response for parameter generation: {response[:500]}")

        try:
            import re

            # Try triple braces first, then plain JSON
            json_match = re.search(r"\{\{\{[\s\S]*?\}\}\}|\{[\s\S]*\}", response)
            if json_match:
                json_str = json_match.group()
                prompt_data = json.loads(json_str)
                self.logger.info(f"Parsed prompt_data: {prompt_data}")

                # Ensure prompt exists
                if "prompt" not in prompt_data or not prompt_data["prompt"].strip():
                    prompt_data["prompt"] = user_query
                    self.logger.warning(f"No prompt in response, using original query: {user_query}")
                if "negative_prompt" not in prompt_data:
                    prompt_data["negative_prompt"] = ""

                return prompt_data, None
            else:
                return None, self._("Could not find JSON in model response", lang)
        except Exception as e:
            self.logger.error(f"JSON parsing error: {str(e)}")
            return None, self._("JSON parsing error: {error}", lang, error=str(e))

    def _call_multimodal(self, messages: list[dict[str, Any]], lang: str = "ru") -> str:
        """Call multimodal model via llama.cpp client (delegates to LlamaCppClient)."""
        # LlamaCppClient handles validation and configuration internally
        return self.llamacpp.chat(messages, model_type="multimodal", lang=lang)  # type: ignore[no-any-return]

    def generate_edit_params(
        self, user_query: str, image_base64: str, lang: str = "ru", response_style: str = "neutral"
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Generate editing parameters for an existing image.
        Uses multimodal model to analyze the image + edit request.
        """
        # Re-check availability with logging
        avail = self.check_availability()
        self.logger.info(
            f"generate_edit_params: check_availability={avail}, llamacpp.available={self.llamacpp.available}"
        )
        if not avail:
            self.logger.warning("Multimodal model unavailable for edit request")
            return None, self._("Multimodal model unavailable", lang)

        edit_prompt = format_prompt(
            "create_image_edit.template",
            {
                "edit_query": user_query,
                "response_style": "",
            },
            lang=lang,
        )

        if not edit_prompt:
            return None, self._("Error loading prompt template", lang)

        response = self.llamacpp.chat_with_image(
            text=edit_prompt, image_base64=image_base64, model_type="multimodal", lang=lang
        )

        self.logger.info(f"Multimodal model edit response: {response[:500]}")

        try:
            import re

            # Try triple braces first, then plain JSON
            json_match = re.search(r"\{\{\{[\s\S]*?\}\}\}|\{[\s\S]*\}", response)
            if json_match:
                json_str = json_match.group()
                edit_data = json.loads(json_str)

                result = {
                    "edit_prompt": edit_data.get("edit_prompt", user_query),
                    "strength": float(edit_data.get("strength", 0.7)),
                    "mask": edit_data.get("mask", ""),
                    "preserve": edit_data.get("preserve", ""),
                }
                self.logger.info(f"Parsed edit params: {result}")
                return result, None
            else:
                return None, self._("Could not find JSON in model response", lang)
        except Exception as e:
            self.logger.error(f"JSON parsing error: {str(e)}")
            return None, self._("JSON parsing error: {error}", lang, error=str(e))

    def check_availability(self) -> bool:
        """Check module availability."""
        return self.llamacpp.check_availability()  # type: ignore[no-any-return]
