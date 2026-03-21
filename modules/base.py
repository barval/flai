# modules/base.py
import logging
import traceback
from typing import Dict, List, Optional, Any, Union

from flask import current_app
from flask_babel import gettext as _
from flask_babel import force_locale

from app.utils import format_prompt, estimate_tokens, build_context_prompt
from app.db import get_session_text_history
from app.ollama_client import OllamaClient


class BaseModule:
    """Base module for chat and reasoning model interactions."""

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.ollama = OllamaClient(app)
        self.available = self.ollama.available
        self.token_chars = 3
        self.context_history_percent = 75
        if app:
            self.init_app(app)

    def _(self, key: str, lang: str = 'ru', **kwargs) -> str:
        """Get translated message using Flask-Babel."""
        with self.app.app_context():
            with force_locale(lang):
                return _(key, **kwargs)

    def init_app(self, app):
        """Initialize module with Flask app."""
        self.app = app
        self.ollama.init_app(app)
        self.available = self.ollama.available
        self.token_chars = app.config.get('TOKEN_CHARS', 3)
        self.context_history_percent = app.config.get('CONTEXT_HISTORY_PERCENT', 75)

        if self.available:
            self.logger.info("BaseModule initialized and available.")
        else:
            self.logger.warning("BaseModule initialized, but Ollama is unavailable")

    def _get_model_config(self, model_type: str = 'chat') -> Optional[Dict[str, Any]]:
        """Retrieve model configuration from database."""
        return self.ollama._get_model_config(model_type)

    def call_ollama(self, messages: List[Dict[str, Any]], model_type: str = 'chat',
                    stream: bool = False, lang: str = 'ru') -> Union[str, Dict[str, Any]]:
        """Call Ollama with configuration."""
        return self.ollama.call(messages, model_type, stream, lang)

    # --- Context handling methods ---
    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimation using configured characters per token."""
        return estimate_tokens(text, self.token_chars)

    def _build_context_prompt(self, history: List[Dict[str, str]], lang: str = 'ru') -> str:
        """Format conversation history into a string."""
        return build_context_prompt(history, lang)

    def _get_context_for_model(self, session_id: str, model_type: str, current_query: str,
                               lang: str = 'ru') -> str:
        """Retrieve and prune conversation history."""
        if not session_id:
            return ""

        model_config = self._get_model_config(model_type)
        if not model_config:
            return ""
        max_context_tokens = model_config.get('context_length', 32768)
        available_tokens = int(max_context_tokens * (self.context_history_percent / 100.0))

        overhead = 500
        query_tokens = self._estimate_tokens(current_query)
        remaining_for_history = available_tokens - query_tokens - overhead
        if remaining_for_history <= 0:
            return ""

        history_msgs = get_session_text_history(session_id, remaining_for_history)
        return self._build_context_prompt(history_msgs, lang)

    # --- Existing methods with context added ---
    def process_message(self, message_text: str, current_time_str: str, lang: str = 'ru',
                        session_id: Optional[str] = None) -> Dict[str, Any]:
        """Process text message through router model."""
        response_language = 'Russian' if lang == 'ru' else 'English'

        context_str = self._get_context_for_model(session_id, 'chat', message_text, lang)

        prompt = format_prompt('base_text.template', {
            'current_time_str': current_time_str,
            'user_query': message_text,
            'response_language': response_language,
            'conversation_history': context_str
        }, lang=lang)

        if not prompt:
            self.logger.error("Error loading prompt template")
            return {'error': self._('Error loading prompt template', lang)}

        router_messages = [
            {'role': 'system', 'content': 'You are a request router. Answer ONLY with one line in the specified language. No explanations.'},
            {'role': 'user', 'content': prompt}
        ]

        self.logger.info(f"Sending request to router: {message_text}")
        router_response = self.call_ollama(router_messages, model_type='chat', lang=lang)
        self.logger.info(f"Router response: {router_response}")

        if router_response is None:
            self.logger.error("Router response is None")
            return {'error': self._('Model returned empty response', lang)}

        return self._parse_router_response(router_response, message_text, current_time_str, lang)

    def _parse_router_response(self, response: str, original_query: str,
                               current_time_str: str, lang: str = 'ru') -> Dict[str, Any]:
        """Parse router response."""
        if response is None:
            self.logger.error("Router response is None in _parse_router_response")
            return {'action': 'none', 'query': '', 'needs_reasoning': False,
                    'error': self._('Model returned empty response', lang)}

        response = response.strip()
        markers = {
            '[-IMAGE-]': 'image',
            '[-CAMERA-]': 'camera',
            '[-REASONING-]': 'reasoning',
            '[-RAG-]': 'rag'
        }

        for marker, action in markers.items():
            if marker in response:
                parts = response.split(marker, 1)
                processed = parts[1].strip() if len(parts) > 1 else ""
                return {
                    'action': action,
                    'query': processed,
                    'needs_reasoning': (action == 'reasoning')
                }

        return {'action': 'none', 'query': response, 'needs_reasoning': False}

    def process_reasoning(self, query: str, current_time_str: str, lang: str = 'ru',
                          session_id: Optional[str] = None) -> str:
        """Process complex query via reasoning model."""
        response_language = 'Russian' if lang == 'ru' else 'English'

        context_str = self._get_context_for_model(session_id, 'reasoning', query, lang)

        reasoning_prompt = format_prompt('reasoning.template', {
            'current_time_str': current_time_str,
            'reasoning_query': query,
            'response_language': response_language,
            'conversation_history': context_str
        }, lang=lang)

        if not reasoning_prompt:
            return "⚠️ " + self._('Error loading prompt template', lang)

        self.logger.info(f"Sending request to reasoning model: {query}")
        response = self.call_ollama([{'role': 'user', 'content': reasoning_prompt}],
                                    model_type='reasoning', lang=lang)
        self.logger.info(f"Reasoning model response: {response[:100]}...")
        return response