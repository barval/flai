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

    def _get_context_for_model(
        self, session_id: str, model_type: str, current_query: str, lang: str = "ru", user_id: str | None = None
    ) -> str:
        """Retrieve conversation history + SLM long-term memory with safety margin."""
        if not session_id:
            return ""

        model_config = self._get_model_config(model_type)
        if not model_config:
            return ""

        max_context_tokens = model_config.get("context_length", 32768)
        slm_recall_limit = self.app.config.get("SLM_RECALL_LIMIT", 3) if hasattr(self, "app") and self.app else 3
        slm_reserve = slm_recall_limit * 70  # ~70 tokens per fact

        # Apply safety margin to available tokens
        available_tokens = int(max_context_tokens * (self.context_history_percent / 100.0) * self.safety_margin)

        query_tokens = self._estimate_tokens(current_query, model_type, lang)
        remaining_for_history = available_tokens - query_tokens - TEMPLATE_OVERHEAD - slm_reserve

        if remaining_for_history <= 0:
            self.logger.warning(
                f"No tokens available for history. Query: {query_tokens}, Available: {available_tokens}"
            )
            return ""

        # Load history with SQL-level limit
        history_msgs = get_session_text_history(session_id, remaining_for_history, max_messages=self.max_messages_limit)
        history_str = self._build_context_prompt(history_msgs, lang) if history_msgs else ""

        # SLM: load long-term memory facts (for both chat and reasoning models)
        slm_facts_str = ""
        slm = self.app.modules.get("slm") if hasattr(self, "app") and self.app else None
        if slm:
            slm_raw = slm.get_context(
                current_query,
                lang,
                limit=slm_recall_limit,
                profile=user_id,
                semantic=(model_type == "reasoning"),
            )
            if slm_raw:
                header = (
                    "Дополнительная информация из долговременной памяти:"
                    if lang == "ru"
                    else "Additional context from long-term memory:"
                )
                slm_facts_str = f"\n\n{header}\n{slm_raw}"

        # Combine: history first (dialog continuity), SLM facts after (long-term enrichment)
        context = history_str + slm_facts_str
        context_tokens = self._estimate_tokens(context, model_type, lang)

        self.logger.info(
            f"Context loaded: {len(history_msgs)} history msgs, "
            f"{context_tokens} tokens ({context_tokens / max_context_tokens * 100:.1f}% of {max_context_tokens})"
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

    def _save_to_slm(self, text: str, metadata: dict[str, Any] | None = None, user_id: str | None = None) -> None:
        """Save a fact to SuperLocalMemory if available."""
        slm = self.app.modules.get("slm") if hasattr(self, "app") and self.app else None
        if slm and slm.available:
            slm.remember(text, metadata=metadata, profile=user_id)

    def _save_to_slm_async(self, text: str, metadata: dict[str, Any] | None = None, user_id: str | None = None) -> None:
        """Save a fact to SLM in a background thread — does not block the response."""
        import threading

        t = threading.Thread(
            target=self._save_to_slm,
            args=(text,),
            kwargs={"metadata": metadata, "user_id": user_id},
            daemon=True,
        )
        t.start()

    # --- Existing methods with context added ---
    def process_message(
        self,
        message_text: str,
        current_time_str: str,
        lang: str = "ru",
        session_id: str | None = None,
        response_style: str = "neutral",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Process text message through router model — no history, no SLM.
        Router only classifies the query; conversation context is handled
        by the downstream chat/reasoning model.
        """
        response_language = "Russian" if lang == "ru" else "English"
        style_instruction = STYLE_INSTRUCTIONS.get(lang, STYLE_INSTRUCTIONS["ru"]).get(
            response_style, STYLE_INSTRUCTIONS[lang]["neutral"]
        )

        prompt = format_prompt(
            "base_text.template",
            {
                "current_time_str": current_time_str,
                "user_query": message_text,
                "response_language": response_language,
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
                "content": "STRICT CLASSIFICATION RULES — You are a query classifier. Output ONLY the result. No explanations, no extra text. SIMPLE queries (greetings, who-are-you, current time) → answer directly WITHOUT any marker. COMPLEX queries (code, math, writing) → use [-REASONING-]. IMAGE/VIDEO/CAMERA → use the appropriate marker. Never output [-REASONING-] for greetings or who-are-you questions. Never copy markers from examples into your response except when the query matches that category.",
            },
            {"role": "user", "content": prompt},
        ]

        self.logger.info(f"Sending request to router: {message_text[:100]}...")
        router_response = self.call_llamacpp(router_messages, model_type="chat", lang=lang)
        self.logger.info(f"Router response: {router_response}")

        # Retry once if router produced a garbled response (rare model inference glitch)
        if (
            isinstance(router_response, str)
            and router_response.strip().startswith('{"error"')
        ):
            self.logger.warning(f"Router returned error, retrying once: {router_response[:100]}")
            router_response = self.call_llamacpp(router_messages, model_type="chat", lang=lang)
            self.logger.info(f"Router retry response: {router_response}")

        if router_response is None:
            self.logger.error("Router response is None")
            return {"error": self._("Model returned empty response", lang)}

        result = self._parse_router_response(router_response, message_text, current_time_str, lang)  # type: ignore[arg-type]
        if "error" not in result:
            self._save_to_slm_async(
                message_text, metadata={"session_id": session_id, "type": "user_query"}, user_id=user_id
            )
        return result

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
                if action in ("image", "video"):
                    # Image/video: text after marker is unreliable (copied from history).
                    # Use the original user query instead.
                    processed = original_query
                elif action == "camera":
                    # Camera: text after marker is the room code ("gos", "kab", etc.)
                    # or the query itself for room name extraction. Use it directly.
                    processed = parts[1].strip() if len(parts) > 1 else ""
                    processed = processed.split("\n")[0].strip()
                    if not processed and original_query:
                        processed = original_query
                else:
                    processed = parts[1].strip() if len(parts) > 1 else ""
                    processed = processed.split("\n")[0].strip()
                    if original_query and len(processed) > len(original_query) * 1.5:
                        processed = original_query
                return {"action": action, "query": processed, "needs_reasoning": (action == "reasoning")}

        return {"action": "none", "query": original_query, "needs_reasoning": False}

    def process_reasoning(
        self,
        query: str,
        current_time_str: str,
        lang: str = "ru",
        session_id: str | None = None,
        response_style: str = "neutral",
        user_id: str | None = None,
        rag_context: str = "",
    ) -> str:
        """Process complex query via reasoning model."""
        response_language = "Russian" if lang == "ru" else "English"
        context_str = self._get_context_for_model(session_id, "reasoning", query, lang, user_id=user_id)  # type: ignore[arg-type]
        style_instruction = STYLE_INSTRUCTIONS.get(lang, STYLE_INSTRUCTIONS["ru"]).get(
            response_style, STYLE_INSTRUCTIONS[lang]["neutral"]
        )

        rag_context_str = rag_context if rag_context else self._("No additional information from documents.", lang)

        reasoning_prompt = format_prompt(
            "reasoning.template",
            {
                "current_time_str": current_time_str,
                "reasoning_query": query,
                "response_language": response_language,
                "conversation_history": context_str,
                "response_style": style_instruction,
                "rag_context": rag_context_str,
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
        user_id: str | None = None,
    ) -> Generator[str, None, None]:
        """Build prompt and stream chat model response."""
        response_language = "Russian" if lang == "ru" else "English"
        context_str = self._get_context_for_model(session_id, "chat", query, lang, user_id=user_id)  # type: ignore[arg-type]
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
        user_id: str | None = None,
    ) -> Generator[str, None, None]:
        """Build prompt and stream reasoning model response."""
        response_language = "Russian" if lang == "ru" else "English"
        context_str = self._get_context_for_model(session_id, "reasoning", query, lang, user_id=user_id)  # type: ignore[arg-type]
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
