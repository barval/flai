# modules/base.py
import logging
from collections.abc import Generator
from typing import Any

from app.db import get_session_text_history
from app.llamacpp_client import LlamaCppClient
from app.mixins import TranslationMixin
from app.utils import (
    SAFETY_MARGIN,
    TEMPLATE_OVERHEAD,
    build_context_prompt,
    estimate_tokens,
    format_prompt,
    validate_prompt_size,
)

STYLE_INSTRUCTIONS = {
    "ru": {
        "neutral": "Без особого стиля.",
        "academic": (
            "Отвечай в формальном академическом стиле. Используй точную терминологию, "
            "строгие формулировки и логически структурированные аргументы. "
            "Избегай разговорных выражений. При необходимости ссылайся на факты."
        ),
        "professional": (
            "Отвечай в профессиональном деловом стиле. Будь чётким, конкретным и по делу. "
            "Используй ясные формулировки. Избегай лишних эмоций и воды."
        ),
        "friendly": (
            "Отвечай в тёплом дружеском стиле. Будь приветлив и располагай к общению. "
            "Используй естественный разговорный тон. Покажи эмпатию и заботу о пользователе. "
            "Можно использовать эмодзи, если они уместны и помогают выразить эмоцию."
        ),
        "funny": (
            "Отвечай с юмором и остроумием. Будь игрив и занимателен. "
            "Используй шутки, метафоры и неожиданные сравнения, но не забывай "
            "давать полезную информацию по существу вопроса. "
            "Эмодзи приветствуются, если они к месту и усиливают эффект."
        ),
    },
    "en": {
        "neutral": "Default style.",
        "academic": (
            "Answer in a formal academic style. Use precise terminology, "
            "rigorous wording, and logically structured arguments. "
            "Avoid colloquial expressions. Reference facts where appropriate."
        ),
        "professional": (
            "Answer in a professional business-like style. Be clear, specific, and to the point. "
            "Use straightforward wording. Avoid unnecessary emotions or fluff."
        ),
        "friendly": (
            "Answer in a warm, friendly style. Be welcoming and approachable. "
            "Use a natural conversational tone. Show empathy and care for the user. "
            "You may use emojis when they are appropriate and help convey emotion."
        ),
        "funny": (
            "Answer with humor and wit. Be playful and entertaining. "
            "Use jokes, metaphors, and unexpected comparisons, "
            "but still provide useful information on the topic. "
            "Emojis are welcome when they fit the context and enhance the effect."
        ),
    },
}


class BaseModule(TranslationMixin):
    """Base module for chat and reasoning model interactions."""

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.llamacpp = LlamaCppClient(app)
        self.available = self.llamacpp.available
        self.token_chars = 3
        self.context_history_percent = 75
        self.safety_margin = SAFETY_MARGIN
        self.max_messages_limit = 30  # Maximum messages to load from history
        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize module with Flask app."""
        self.app = app
        self.llamacpp.init_app(app)
        self.available = self.llamacpp.available
        self.token_chars = app.config.get("TOKEN_CHARS", 3)
        self.context_history_percent = app.config.get("CONTEXT_HISTORY_PERCENT", 75)
        self.safety_margin = app.config.get("CONTEXT_SAFETY_MARGIN", SAFETY_MARGIN)
        self.max_messages_limit = app.config.get("MAX_HISTORY_MESSAGES", 30)
        if self.available:
            self.logger.info("BaseModule initialized and available.")
        else:
            self.logger.warning("BaseModule initialized, but llama-server is unavailable")

    def _get_model_config(self, model_type: str = "chat") -> dict[str, Any] | None:
        """Retrieve model configuration from database."""
        from app.model_config import get_model_config

        return get_model_config(model_type)  # type: ignore[no-any-return]

    def call_llamacpp(
        self, messages: list[dict[str, Any]], model_type: str = "chat", lang: str = "ru"
    ) -> str | dict[str, Any]:
        """Call llama-server with configuration."""
        return self.llamacpp.call(messages, model_type, False, lang)  # type: ignore[no-any-return]

    # --- Context handling methods ---
    def _estimate_tokens(self, text: str, model_type: str = "chat", lang: str = "ru") -> int:
        """Token estimation with language and model-specific coefficients."""
        return estimate_tokens(text, model_type, lang, self.token_chars)

    def _build_context_prompt(self, history: list[dict[str, str]], lang: str = "ru") -> str:
        """Format conversation history into a string."""
        return build_context_prompt(history, lang)

    def _get_context_for_model(self, session_id: str, model_type: str, current_query: str, lang: str = "ru") -> str:
        """Retrieve and prune conversation history with safety margin."""
        if not session_id:
            return ""

        model_config = self._get_model_config(model_type)
        if not model_config:
            return ""

        max_context_tokens = model_config.get("context_length", 32768)

        # Apply safety margin to available tokens
        available_tokens = int(max_context_tokens * (self.context_history_percent / 100.0) * self.safety_margin)

        query_tokens = self._estimate_tokens(current_query, model_type, lang)
        remaining_for_history = available_tokens - query_tokens - TEMPLATE_OVERHEAD

        if remaining_for_history <= 0:
            self.logger.warning(
                f"No tokens available for history. Query: {query_tokens}, Available: {available_tokens}"
            )
            return ""

        # Load history with SQL-level limit
        history_msgs = get_session_text_history(session_id, remaining_for_history, max_messages=self.max_messages_limit)

        context = self._build_context_prompt(history_msgs, lang)
        context_tokens = self._estimate_tokens(context, model_type, lang)

        self.logger.info(
            f"Context loaded: {len(history_msgs)} messages, {context_tokens} tokens "
            f"({context_tokens / max_context_tokens * 100:.1f}% of {max_context_tokens})"
        )

        return context

    def _validate_final_prompt(self, prompt: str, model_type: str = "chat", lang: str = "ru") -> str | None:
        """
        Validate final prompt before sending to llama-server.
        Returns None if valid, error message string if invalid.
        """
        model_config = self._get_model_config(model_type)
        is_valid, estimated, max_tokens = validate_prompt_size(prompt, model_config, model_type, lang)  # type: ignore[arg-type]

        if not is_valid:
            error_msg = f"Prompt too large: {estimated} tokens (max: {int(max_tokens * 0.95)})"
            self.logger.error(error_msg)
            return self._("Request too long, please simplify your request", lang)

        self.logger.info(
            f"Prompt validation passed: {estimated}/{max_tokens} tokens ({estimated / max_tokens * 100:.1f}%)"
        )
        return None

    # --- Existing methods with context added ---
    def process_message(
        self,
        message_text: str,
        current_time_str: str,
        lang: str = "ru",
        session_id: str | None = None,
        response_style: str = "neutral",
    ) -> dict[str, Any]:
        """Process text message through router model."""
        response_language = "Russian" if lang == "ru" else "English"
        context_str = self._get_context_for_model(session_id, "chat", message_text, lang)  # type: ignore[arg-type]
        style_instruction = STYLE_INSTRUCTIONS.get(lang, STYLE_INSTRUCTIONS["ru"]).get(
            response_style, STYLE_INSTRUCTIONS[lang]["neutral"]
        )

        prompt = format_prompt(
            "base_text.template",
            {
                "current_time_str": current_time_str,
                "user_query": message_text,
                "response_language": response_language,
                "conversation_history": context_str,
                "response_style": style_instruction,
            },
            lang=lang,
        )

        if not prompt:
            self.logger.error("Error loading prompt template")
            return {"error": self._("Error loading prompt template", lang)}

        # Validate final prompt before sending
        error = self._validate_final_prompt(prompt, "chat", lang)
        if error:
            return {"error": error}

        router_messages = [
            {
                "role": "system",
                "content": "You are a request router. Answer ONLY with one line in the specified language. No explanations.",
            },
            {"role": "user", "content": prompt},
        ]

        self.logger.info(f"Sending request to router: {message_text[:100]}...")
        router_response = self.call_llamacpp(router_messages, model_type="chat", lang=lang)
        self.logger.info(f"Router response: {router_response}")

        if router_response is None:
            self.logger.error("Router response is None")
            return {"error": self._("Model returned empty response", lang)}

        return self._parse_router_response(router_response, message_text, current_time_str, lang)  # type: ignore[arg-type]

    def _parse_router_response(
        self, response: str, original_query: str, current_time_str: str, lang: str = "ru"
    ) -> dict[str, Any]:
        """Parse router response."""
        if response is None:
            self.logger.error("Router response is None in _parse_router_response")
            return {
                "action": "none",
                "query": "",
                "needs_reasoning": False,
                "error": self._("Model returned empty response", lang),
            }

        response = response.strip()
        markers = {
            "[-IMAGE-]": "image",
            "[-CAMERA-]": "camera",
            "[-REASONING-]": "reasoning",
            "[-RAG-]": "rag",
            "[-VIDEO-]": "video",
        }

        for marker, action in markers.items():
            if marker in response:
                parts = response.split(marker, 1)
                processed = parts[1].strip() if len(parts) > 1 else ""
                return {"action": action, "query": processed, "needs_reasoning": (action == "reasoning")}

        return {"action": "none", "query": response, "needs_reasoning": False}

    def process_reasoning(
        self,
        query: str,
        current_time_str: str,
        lang: str = "ru",
        session_id: str | None = None,
        response_style: str = "neutral",
    ) -> str:
        """Process complex query via reasoning model."""
        response_language = "Russian" if lang == "ru" else "English"
        context_str = self._get_context_for_model(session_id, "reasoning", query, lang)  # type: ignore[arg-type]
        style_instruction = STYLE_INSTRUCTIONS.get(lang, STYLE_INSTRUCTIONS["ru"]).get(
            response_style, STYLE_INSTRUCTIONS[lang]["neutral"]
        )

        reasoning_prompt = format_prompt(
            "reasoning.template",
            {
                "current_time_str": current_time_str,
                "reasoning_query": query,
                "response_language": response_language,
                "conversation_history": context_str,
                "response_style": style_instruction,
            },
            lang=lang,
        )

        if not reasoning_prompt:
            return "⚠️ " + self._("Error loading prompt template", lang)

        # Validate final prompt before sending
        error = self._validate_final_prompt(reasoning_prompt, "reasoning", lang)
        if error:
            return "⚠️ " + error

        self.logger.info(f"Sending request to reasoning model: {query[:100]}...")
        response = self.call_llamacpp(
            [{"role": "user", "content": reasoning_prompt}], model_type="reasoning", lang=lang
        )
        self.logger.info(f"Reasoning model response: {response[:100]}...")  # type: ignore[index]
        return response  # type: ignore[return-value]

    # ── Streaming methods ──────────────────────────────────────────────

    def generate_chat_response_stream(
        self,
        query: str,
        current_time_str: str,
        lang: str = "ru",
        session_id: str | None = None,
        response_style: str = "neutral",
    ) -> Generator[str, None, None]:
        """Build prompt and stream chat model response."""
        response_language = "Russian" if lang == "ru" else "English"
        context_str = self._get_context_for_model(session_id, "chat", query, lang)  # type: ignore[arg-type]
        style_instruction = STYLE_INSTRUCTIONS.get(lang, STYLE_INSTRUCTIONS["ru"]).get(
            response_style, STYLE_INSTRUCTIONS[lang]["neutral"]
        )

        prompt = format_prompt(
            "chat.template",
            {
                "current_time_str": current_time_str,
                "user_query": query,
                "response_language": response_language,
                "conversation_history": context_str,
                "response_style": style_instruction,
            },
            lang=lang,
        )

        if not prompt:
            yield "⚠️ " + self._("Error loading prompt template", lang)
            return

        error = self._validate_final_prompt(prompt, "chat", lang)
        if error:
            yield "⚠️ " + error
            return

        self.logger.info(f"Streaming chat response for query: {query[:100]}...")
        yield from self.llamacpp.chat_stream([{"role": "user", "content": prompt}], model_type="chat", lang=lang)

    def generate_reasoning_response_stream(
        self,
        query: str,
        current_time_str: str,
        lang: str = "ru",
        session_id: str | None = None,
        response_style: str = "neutral",
    ) -> Generator[str, None, None]:
        """Build prompt and stream reasoning model response."""
        response_language = "Russian" if lang == "ru" else "English"
        context_str = self._get_context_for_model(session_id, "reasoning", query, lang)  # type: ignore[arg-type]
        style_instruction = STYLE_INSTRUCTIONS.get(lang, STYLE_INSTRUCTIONS["ru"]).get(
            response_style, STYLE_INSTRUCTIONS[lang]["neutral"]
        )

        prompt = format_prompt(
            "reasoning.template",
            {
                "current_time_str": current_time_str,
                "reasoning_query": query,
                "response_language": response_language,
                "conversation_history": context_str,
                "response_style": style_instruction,
            },
            lang=lang,
        )

        if not prompt:
            yield "⚠️ " + self._("Error loading prompt template", lang)
            return

        error = self._validate_final_prompt(prompt, "reasoning", lang)
        if error:
            yield "⚠️ " + error
            return

        self.logger.info(f"Streaming reasoning response for query: {query[:100]}...")
        yield from self.llamacpp.chat_stream([{"role": "user", "content": prompt}], model_type="reasoning", lang=lang)
