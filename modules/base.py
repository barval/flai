# modules/base.py
import logging
import time
from collections.abc import Callable, Generator
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
        "neutral": (
            "Отвечай нейтральным ясным языком. Не проявляй лишних эмоций и не старайся быть формальным — "
            "просто давай точный и полезный ответ по существу."
        ),
        "academic": (
            "Отвечай в формальном академическом стиле. Используй точную терминологию, "
            "строгие формулировки и логически структурированные аргументы. "
            "Ссылайся на факты и источники, когда уместно. "
            "НЕ используй разговорные выражения, сленг, эмодзи и обращения на 'ты'."
        ),
        "professional": (
            "Отвечай в профессиональном деловом стиле. Будь чётким, конкретным и по делу. "
            "Используй ясные формулировки и структурированные списки при необходимости. "
            "НЕ используй эмодзи, шутки, разговорные выражения и лишние эмоции."
        ),
        "friendly": (
            "Отвечай в тёплом дружеском стиле. Обращайся на 'ты', будь приветлив и располагай к общению. "
            "Используй естественный разговорный тон, показывай эмпатию и заботу. "
            "Эмодзи уместны, если усиливают эмоцию. "
            "НЕ отвечай сухо, формально или как в инструкции — это должно ощущаться как живой разговор."
        ),
        "funny": (
            "Отвечай с юмором и остроумием. Будь игрив, используй шутки, метафоры и неожиданные сравнения. "
            "Эмодзи приветствуются, если усиливают эффект. "
            "Всегда давай полезную информацию по существу вопроса — юмор не заменяет содержание. "
            "НЕ отвечай серьёзно или сухо — шутка или ирония должны быть заметны."
        ),
    },
    "en": {
        "neutral": (
            "Answer in clear, neutral language. No extra emotions, no formality — just a precise, useful answer."
        ),
        "academic": (
            "Answer in a formal academic style. Use precise terminology, "
            "rigorous wording, and logically structured arguments. "
            "Reference facts and sources where appropriate. "
            "Do NOT use colloquial expressions, slang, emojis, or informal tone."
        ),
        "professional": (
            "Answer in a professional business-like style. Be clear, specific, and to the point. "
            "Use straightforward wording and structured lists when helpful. "
            "Do NOT use emojis, jokes, colloquial expressions, or unnecessary emotions."
        ),
        "friendly": (
            "Answer in a warm, friendly style. Be welcoming, approachable, and conversational. "
            "Show empathy and care for the user. Use a natural tone as if talking to a friend. "
            "Emojis are welcome when they convey genuine emotion. "
            "Do NOT sound dry, formal, or robotic — this should feel like a real human conversation."
        ),
        "funny": (
            "Answer with humor, wit, and playfulness. Use jokes, metaphors, and unexpected comparisons. "
            "Emojis are welcome when they enhance the effect. "
            "Always provide useful, on-topic information — humor should not replace substance. "
            "Do NOT answer seriously or dryly — the joke or irony should be noticeable."
        ),
    },
}


def get_style_instruction(lang: str, response_style: str) -> str:
    """Look up the style instruction for a language/style pair (neutral fallback)."""
    styles = STYLE_INSTRUCTIONS.get(lang, STYLE_INSTRUCTIONS["ru"])
    return styles.get(response_style, styles["neutral"])


def response_language_name(lang: str) -> str:
    """Human-readable language name used in prompts."""
    return "Russian" if lang == "ru" else "English"


def wait_for_service(
    check_fn: Callable[[], bool],
    logger: logging.Logger,
    service_name: str,
    retries: int,
    delay: float,
) -> bool:
    """Retry ``check_fn`` until it succeeds or retries are exhausted.

    Used during module init for services that may start slower than the
    web app. Returns True on the first successful check, False otherwise.
    """
    for attempt in range(1, retries + 1):
        if check_fn():
            return True
        if attempt < retries:
            logger.warning(f"{service_name} not ready (attempt {attempt}/{retries}), retrying in {delay}s...")
            time.sleep(delay)
    logger.warning(f"{service_name} not available after {retries} attempts")
    return False


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

    def _get_model_config(self, model_type: str = "multimodal") -> dict[str, Any] | None:
        """Retrieve model configuration from database."""
        from app.model_config import get_model_config

        return get_model_config(model_type)  # type: ignore[no-any-return]

    def call_llamacpp(
        self,
        messages: list[dict[str, Any]],
        model_type: str = "multimodal",
        lang: str = "ru",
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
    ) -> str | dict[str, Any]:
        """Call llama-server with configuration."""
        return self.llamacpp.call(messages, model_type, False, lang, tools=tools, temperature=temperature)  # type: ignore[no-any-return]

    # --- Context handling methods ---
    def get_search_context_limit(self) -> int:
        """Compute max chars for search context based on reasoning model's context window.

        Uses ~30% of the effective context budget (after margins) to leave room
        for template, history, and SLM facts. Assumes ~3.5 chars/token for Russian.
        Falls back to 10 000 if config is unavailable.
        """
        config = self._get_model_config("reasoning")
        if not config:
            return 10000
        ctx = config.get("context_length", 16384)
        budget = int(ctx * (self.context_history_percent / 100.0) * self.safety_margin)
        return int(budget * 0.30 * 3.5)

    def _estimate_tokens(self, text: str, model_type: str = "multimodal", lang: str = "ru") -> int:
        """Token estimation with language and model-specific coefficients."""
        return estimate_tokens(text, model_type, lang, self.token_chars)

    def _build_context_prompt(self, history: list[dict[str, str]], lang: str = "ru") -> str:
        """Format conversation history into a string."""
        return build_context_prompt(history, lang)

    def _get_context_for_model(
        self,
        session_id: str,
        model_type: str,
        current_query: str,
        lang: str = "ru",
        user_id: str | None = None,
        skip_slm: bool = False,
        rag_context: str = "",
        rag_source: str = "",
    ) -> str:
        """Retrieve conversation history + SLM long-term memory with safety margin.

        Budget allocation order: query → template overhead → RAG context → SLM facts → history (last).
        History is trimmed to fit whatever remains after all other components are measured.
        """
        if not session_id:
            return ""

        model_config = self._get_model_config(model_type)
        if not model_config:
            return ""

        max_context_tokens = model_config.get("context_length", 32768)
        slm_recall_limit = self.app.config.get("SLM_RECALL_LIMIT", 3) if hasattr(self, "app") and self.app else 3

        # Apply safety margin to available tokens
        available_tokens = int(max_context_tokens * (self.context_history_percent / 100.0) * self.safety_margin)
        query_tokens = self._estimate_tokens(current_query, model_type, lang)

        # Step 1: Measure RAG context tokens (internet search results)
        rag_tokens = 0
        if rag_context:
            rag_tokens = self._estimate_tokens(rag_context, model_type, lang)

        # Step 2: Fetch SLM facts first to measure their real size
        all_facts: list[dict[str, Any]] = []
        if not skip_slm:
            slm = self.app.modules.get("slm") if hasattr(self, "app") and self.app else None
            if slm:
                # Single recall with doubled limit, then local filter by type
                raw_facts = slm.recall(
                    current_query,
                    limit=slm_recall_limit * 2,
                    profile=user_id,
                    semantic=True,
                )

                session_facts = [f for f in raw_facts if f.get("metadata", {}).get("fact_type") == "session_specific"]
                general_facts = [f for f in raw_facts if f.get("metadata", {}).get("fact_type") != "session_specific"]

                all_facts = session_facts + general_facts

        # Step 3: Build SLM string and measure its real token cost
        slm_facts_str = ""
        slm_tokens = 0
        if all_facts:
            header = (
                "Дополнительная информация из долговременной памяти:"
                if lang == "ru"
                else "Additional context from long-term memory:"
            )
            lines = [header]
            for f in all_facts[:slm_recall_limit]:
                lines.append(f"- {f.get('content', f.get('text', ''))}")
            slm_facts_str = "\n" + "\n".join(lines)
            slm_tokens = self._estimate_tokens(slm_facts_str, model_type, lang)

        # Step 4: Build RAG section (needed before budget check for fallback)
        rag_section = ""
        if rag_context:
            if rag_source == "web_search":
                heading = (
                    "Результаты поиска в интернете — используй эти данные как основной источник. "
                    "Если данных недостаточно, можешь дополнить ответ своими знаниями, но не выдумывай факты."
                    if lang == "ru"
                    else "Web search results — use this data as your primary source. "
                    "If the data is insufficient, you may supplement with your own knowledge, but do not fabricate facts."
                )
            else:
                heading = "Найденная информация из документов:" if lang == "ru" else "Found information from documents:"
            rag_section = "\n" + heading + "\n" + rag_context

        # Step 5: Calculate history budget — subtract query, template, RAG, and SLM
        remaining_for_history = available_tokens - query_tokens - TEMPLATE_OVERHEAD - rag_tokens - slm_tokens

        if remaining_for_history <= 0:
            self.logger.warning(
                f"No tokens available for history. Query: {query_tokens}, RAG: {rag_tokens}, "
                f"SLM: {slm_tokens}, Available: {available_tokens} — returning RAG+SLM without history"
            )
            context = rag_section + slm_facts_str
            return context.lstrip()

        # Step 6: Load history with SQL-level limit based on remaining budget
        history_msgs = get_session_text_history(session_id, remaining_for_history, max_messages=self.max_messages_limit)
        history_str = self._build_context_prompt(history_msgs, lang) if history_msgs else ""

        context = rag_section + slm_facts_str + history_str
        history_tokens = self._estimate_tokens(history_str, model_type, lang)
        context_tokens = self._estimate_tokens(context, model_type, lang)

        self.logger.info(
            f"Context loaded: {len(history_msgs)} history msgs ({history_tokens} tokens), "
            f"{len(all_facts)} SLM facts ({slm_tokens} tokens), "
            + (f"{'Web search' if rag_source == 'web_search' else 'RAG'} ({rag_tokens} tokens), " if rag_tokens else "")
            + f"TOTAL: {context_tokens} tokens ({context_tokens / max_context_tokens * 100:.1f}% of {max_context_tokens})"
        )

        return context

    def _validate_final_prompt(self, prompt: str, model_type: str = "multimodal", lang: str = "ru") -> str | None:
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

    def _build_camera_prompt_section(self, lang: str = "ru") -> str:
        """Build the camera classification section for the router prompt.

        Reads room definitions from the CamModule (loaded from DB)
        and generates category 5 with all configured rooms and their
        name forms — no hardcoded room names.
        """
        cam = self.app.modules.get("cam") if hasattr(self, "app") and self.app else None  # type: ignore[attr-defined]
        if not cam or not cam.available:
            return ""

        rooms_with_forms = cam.get_all_rooms_with_forms()
        if not rooms_with_forms:
            return ""

        if lang == "ru":
            lines = [
                "## 4. ЗАПРОС НА ПРОСМОТР КАМЕРЫ (ПРИОРИТЕТ — Даже если есть «?»)",
                "Если запрос содержит:",
                "  (а) упоминание любой комнаты из списка ниже, И",
                "  (б) любой вариант просьбы показать/посмотреть/узнать о комнате",
                "      (что, как, как дела, покажи, выведи, посмотри, проверь, есть ли кто, что происходит, и т.д.)",
                "Тогда это запрос к камере, а НЕ обычный вопрос.",
                "Действие: выведи ТОЛЬКО [-CAMERA-] и код комнаты.",
                "",
                "Комнаты (из БД):",
            ]
            for code, forms in rooms_with_forms:
                forms_str = " / ".join(forms)
                lines.append(f'  - "{forms_str}" → [-CAMERA-] {code}')
            lines.append("")
            lines.append("Примеры:")
            lines.append('  - "Покажи кабинет" → [-CAMERA-] kab')
            lines.append('  - "Что в гостиной" → [-CAMERA-] gos')
            lines.append('  - "как дела в гостиной?" → [-CAMERA-] gos')
            lines.append('  - "как в тамбуре?" → [-CAMERA-] tam')
            lines.append('  - "Есть ли кто в тамбуре" → [-CAMERA-] tam')
            lines.append('  - "Что происходит в кабинете?" → [-CAMERA-] kab')
            lines.append('  - "Покажи гараж" → Покажи гараж')
        else:
            lines = [
                "## 4. CAMERA VIEW REQUEST (PRIORITY — even with '?')",
                "If the query contains:",
                "  (a) mention of any room from the list below, AND",
                "  (b) any variant of asking to show/view/check the room",
                "      (what, how, how's it, show, display, look, check, is anyone there, what's happening, etc.)",
                "Then this is a camera request, NOT a regular question.",
                "Action: output ONLY [-CAMERA-] and the room code.",
                "",
                "Rooms (from DB):",
            ]
            for code, forms in rooms_with_forms:
                forms_str = " / ".join(forms)
                lines.append(f'  - "{forms_str}" → [-CAMERA-] {code}')
            lines.append("")
            lines.append("Examples:")
            lines.append('  - "Show the study" → [-CAMERA-] kab')
            lines.append('  - "What\'s in the living room" → [-CAMERA-] gos')
            lines.append('  - "how\'s the living room?" → [-CAMERA-] gos')
            lines.append('  - "how\'s the vestibule?" → [-CAMERA-] tam')
            lines.append('  - "Is anyone in the vestibule" → [-CAMERA-] tam')
            lines.append('  - "What\'s happening in the study?" → [-CAMERA-] kab')
            lines.append('  - "Show the garage" → Show the garage')

        return "\n".join(lines)

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
        response_language = response_language_name(lang)
        style_instruction = get_style_instruction(lang, response_style)

        camera_section = self._build_camera_prompt_section(lang)

        prompt = format_prompt(
            "base_text.template",
            {
                "current_time_str": current_time_str,
                "user_query": message_text,
                "response_language": response_language,
                "response_style": style_instruction,
                "camera_section": camera_section,
            },
            lang=lang,
        )

        if not prompt:
            self.logger.error("Error loading prompt template")
            return {"error": self._("Error loading prompt template", lang)}

        # Validate final prompt before sending
        error = self._validate_final_prompt(prompt, "multimodal", lang)
        if error:
            return {"error": error}

        router_messages = [
            {
                "role": "system",
                "content": "STRICT CLASSIFICATION RULES — You are a query classifier. Output ONLY the result. No explanations, no extra text. SIMPLE queries (greetings, who-are-you, time/date, skills) → answer WITHOUT any marker. NEVER use [-REASONING-] for time or date questions. IMAGE generation → use [-IMAGE-]. VIDEO generation → use [-VIDEO-]. CAMERA/snapshot → use [-CAMERA-]. DOCUMENT search → use [-RAG-]. WEB search (news, prices, latest info) → use [-SEARCH-]. COMPLEX tasks (code, writing, reasoning) → use [-REASONING-]. REMEMBER requests → use [-REMEMBER-]. Never output reasoning markers for simple queries.",
            },
            {"role": "user", "content": prompt},
        ]

        self.logger.info(f"Sending request to router: {message_text[:100]}...")
        router_response = self.call_llamacpp(router_messages, model_type="multimodal", lang=lang, temperature=0.1)
        self.logger.info(f"Router response: {router_response}")

        # Retry once if router produced a garbled response (rare model inference glitch)
        if isinstance(router_response, str) and router_response.strip().startswith('{"error"'):
            self.logger.warning(f"Router returned error, retrying once: {router_response[:100]}")
            router_response = self.call_llamacpp(router_messages, model_type="multimodal", lang=lang, temperature=0.1)
            self.logger.info(f"Router retry response: {router_response}")

        if router_response is None:
            self.logger.error("Router response is None")
            return {"error": self._("Model returned empty response", lang)}

        result = self._parse_router_response(router_response, message_text, current_time_str, lang)  # type: ignore[arg-type]
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
            "[-SEARCH-]": "search",
            "[-VIDEO-]": "video",
            "[-REMEMBER-]": "remember",
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
                    # Fallback: if LLM returned full query instead of room code,
                    # try to extract room code via cam module
                    cam = self.app.modules.get("cam") if hasattr(self, "app") and self.app else None  # type: ignore[attr-defined]
                    if cam and hasattr(cam, "room_names") and processed not in cam.room_names:
                        extracted = cam.get_room_code(processed)
                        if extracted:
                            processed = extracted
                else:
                    processed = parts[1].strip() if len(parts) > 1 else ""
                    processed = processed.split("\n")[0].strip()
                    if original_query and (not processed or len(processed) > len(original_query) * 1.5):
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
        rag_source: str = "",
    ) -> str:
        """Process complex query via reasoning model."""
        response_language = response_language_name(lang)
        context_str = self._get_context_for_model(
            session_id or "",
            "reasoning",
            query,
            lang,
            user_id=user_id,
            rag_context=rag_context,
            rag_source=rag_source,
        )
        style_instruction = get_style_instruction(lang, response_style)

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
        self.logger.info(f"Reasoning model response: {str(response)[:100]}...")
        return str(response)

    # ── Streaming methods ──────────────────────────────────────────────

    def generate_reasoning_response_stream(
        self,
        query: str,
        current_time_str: str,
        lang: str = "ru",
        session_id: str | None = None,
        response_style: str = "neutral",
        user_id: str | None = None,
        rag_context: str = "",
        rag_source: str = "",
        ensure_vram: bool = True,
    ) -> Generator[str, None, None]:
        """Build prompt and stream reasoning model response.

        ensure_vram=False reuses an already-loaded reasoning model (used by the
        empty-output retry in queue.py to avoid an unload/reload between attempts).
        """
        response_language = response_language_name(lang)
        context_str = self._get_context_for_model(
            session_id or "",
            "reasoning",
            query,
            lang,
            user_id=user_id,
            rag_context=rag_context,
            rag_source=rag_source,
        )
        style_instruction = get_style_instruction(lang, response_style)

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
        yield from self.llamacpp.chat_stream(
            [{"role": "user", "content": prompt}],
            model_type="reasoning",
            lang=lang,
            ensure_vram=ensure_vram,
        )
