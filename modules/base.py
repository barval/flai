# modules/base.py
import logging
import traceback
from typing import Dict, List, Optional, Any, Union, Tuple  # Tuple added
from flask import current_app
from flask_babel import gettext as _
from flask_babel import force_locale
from app.utils import format_prompt, estimate_tokens, build_context_prompt, validate_prompt_size, SAFETY_MARGIN, TEMPLATE_OVERHEAD
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
        self.safety_margin = SAFETY_MARGIN
        self.max_messages_limit = 30  # Maximum messages to load from history
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
        self.safety_margin = app.config.get('CONTEXT_SAFETY_MARGIN', SAFETY_MARGIN)
        self.max_messages_limit = app.config.get('MAX_HISTORY_MESSAGES', 30)
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
    def _estimate_tokens(self, text: str, model_type: str = 'chat', lang: str = 'ru') -> int:
        """Token estimation with language and model-specific coefficients."""
        return estimate_tokens(text, model_type, lang, self.token_chars)
    
    def _build_context_prompt(self, history: List[Dict[str, str]], lang: str = 'ru') -> str:
        """Format conversation history into a string."""
        return build_context_prompt(history, lang)
    
    def _get_context_for_model(self, session_id: str, model_type: str, current_query: str,
                               lang: str = 'ru') -> str:
        """Retrieve and prune conversation history with safety margin."""
        if not session_id:
            return ""
        
        model_config = self._get_model_config(model_type)
        if not model_config:
            return ""
        
        max_context_tokens = model_config.get('context_length', 32768)
        
        # Apply safety margin to available tokens
        available_tokens = int(max_context_tokens * (self.context_history_percent / 100.0) * self.safety_margin)
        
        query_tokens = self._estimate_tokens(current_query, model_type, lang)
        remaining_for_history = available_tokens - query_tokens - TEMPLATE_OVERHEAD
        
        if remaining_for_history <= 0:
            self.logger.warning(f"No tokens available for history. Query: {query_tokens}, Available: {available_tokens}")
            return ""
        
        # Load history with SQL-level limit
        history_msgs = get_session_text_history(
            session_id, 
            remaining_for_history, 
            max_messages=self.max_messages_limit
        )
        
        context = self._build_context_prompt(history_msgs, lang)
        context_tokens = self._estimate_tokens(context, model_type, lang)
        
        self.logger.info(f"Context loaded: {len(history_msgs)} messages, {context_tokens} tokens "
                        f"({context_tokens/max_context_tokens*100:.1f}% of {max_context_tokens})")
        
        return context
    
    def _validate_final_prompt(self, prompt: str, model_type: str = 'chat', lang: str = 'ru') -> Tuple[bool, str]:
        """
        Validate final prompt before sending to Ollama.
        Returns (is_valid, error_message or prompt)
        """
        model_config = self._get_model_config(model_type)
        is_valid, estimated, max_tokens = validate_prompt_size(prompt, model_config, model_type, lang)
        
        if not is_valid:
            error_msg = f"Prompt too large: {estimated} tokens (max: {int(max_tokens * 0.95)})"
            self.logger.error(error_msg)
            return False, self._('Request too long, please simplify your request', lang)
        
        self.logger.info(f"Prompt validation passed: {estimated}/{max_tokens} tokens ({estimated/max_tokens*100:.1f}%)")
        return True, prompt

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
        
        # Validate final prompt before sending
        is_valid, result = self._validate_final_prompt(prompt, 'chat', lang)
        if not is_valid:
            return {'error': result}
        
        router_messages = [
            {'role': 'system', 'content': 'You are a request router. Answer ONLY with one line in the specified language. No explanations.'},
            {'role': 'user', 'content': result}
        ]
        
        self.logger.info(f"Sending request to router: {message_text[:100]}...")
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
        
        # Validate final prompt before sending
        is_valid, result = self._validate_final_prompt(reasoning_prompt, 'reasoning', lang)
        if not is_valid:
            return "⚠️ " + result
        
        self.logger.info(f"Sending request to reasoning model: {query[:100]}...")
        response = self.call_ollama([{'role': 'user', 'content': result}],
                                    model_type='reasoning', lang=lang)
        self.logger.info(f"Reasoning model response: {response[:100]}...")
        return response