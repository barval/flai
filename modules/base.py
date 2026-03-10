# modules/base.py
import logging
import requests
from datetime import datetime
import os
from flask import current_app
from flask_babel import gettext as _
from flask_babel import force_locale

from app.utils import format_prompt
from app.db import get_session_text_history  # new import

class BaseModule:
    """Base module for chat and reasoning model interactions"""
    
    def __init__(self, app=None, ollama_url=None, models_config=None):
        self.logger = logging.getLogger(__name__)
        self.ollama_url = ollama_url
        self.models_config = models_config or {}
        self.available = False
        self.timeouts = {}
        self.token_chars = 3
        self.context_history_percent = 75
        if app:
            self.init_app(app)
        elif ollama_url:
            self.check_availability()

    def _(self, key, lang='ru', **kwargs):
        """Get translated message using Flask-Babel."""
        with self.app.app_context():
            with force_locale(lang):
                return _(key, **kwargs)
    
    def init_app(self, app):
        """Initialize module with Flask app"""
        self.app = app
        self.ollama_url = app.config.get('OLLAMA_URL')
        
        self.timeouts = {
            'chat': app.config.get('LLM_CHAT_TIMEOUT', 60),
            'multimodal': app.config.get('LLM_MULTIMODAL_TIMEOUT', 120),
            'reasoning': app.config.get('LLM_REASONING_TIMEOUT', 300)
        }
        
        self.models_config = {
            'chat': {
                'model': app.config.get('LLM_CHAT_MODEL'),
                'context': app.config.get('LLM_CHAT_MODEL_CONTEXT_WINDOW', 32768),
                'temperature': app.config.get('LLM_CHAT_TEMPERATURE', 0.1),
                'top_p': app.config.get('LLM_CHAT_TOP_P', 0.1),
                'timeout': self.timeouts['chat']
            },
            'reasoning': {
                'model': app.config.get('LLM_REASONING_MODEL'),
                'context': app.config.get('LLM_REASONING_MODEL_CONTEXT_WINDOW', 40960),
                'temperature': app.config.get('LLM_REASONING_TEMPERATURE', 0.7),
                'top_p': app.config.get('LLM_REASONING_TOP_P', 0.9),
                'timeout': self.timeouts['reasoning']
            },
            'multimodal': {
                'model': app.config.get('LLM_MULTIMODAL_MODEL', app.config.get('LLM_MULTIMODAL_MODEL')),
                'context': app.config.get('LLM_MULTIMODAL_MODEL_CONTEXT_WINDOW', 32768),
                'temperature': app.config.get('LLM_MULTIMODAL_TEMPERATURE', 0.7),
                'top_p': app.config.get('LLM_MULTIMODAL_TOP_P', 0.9),
                'timeout': self.timeouts.get('multimodal', 120)
            }
        }
        
        # Token estimation settings
        self.token_chars = app.config.get('TOKEN_CHARS', 3)
        self.context_history_percent = app.config.get('CONTEXT_HISTORY_PERCENT', 75)
        
        self.check_availability()
        
        if self.available:
            self.logger.info(f"BaseModule initialized and available. Timeouts: {self.timeouts}")
        else:
            self.logger.warning("BaseModule initialized, but Ollama is unavailable")
    
    def check_availability(self):
        """Check module availability"""
        if not self.ollama_url:
            self.logger.error("OLLAMA_URL not configured")
            return False
        
        try:
            self.logger.info(f"Checking connection to Ollama at: {self.ollama_url}")
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if response.status_code == 200:
                models = response.json().get('models', [])
                available_models = [m['name'] for m in models]
                
                chat_model = self.models_config['chat']['model']
                reasoning_model = self.models_config['reasoning']['model']
                
                self.logger.info(f"Available models in Ollama: {available_models}")
                
                if chat_model not in available_models:
                    self.logger.warning(f"Chat model {chat_model} not found in Ollama")
                
                if reasoning_model not in available_models:
                    self.logger.warning(f"Reasoning model {reasoning_model} not found in Ollama")
                
                self.available = True
                return True
            else:
                self.logger.error(f"Ollama returned status {response.status_code}")
        except requests.exceptions.ConnectionError:
            self.logger.error(f"Connection error to Ollama at {self.ollama_url}")
        except Exception as e:
            self.logger.error(f"Error connecting to Ollama: {str(e)}")
        
        self.available = False
        return False
    
    def call_ollama(self, messages, model_type='chat', stream=False, lang='ru'):
        """Call Ollama API with configurable timeout"""
        if not self.available:
            self.check_availability()
            if not self.available:
                return self._('Ollama service unavailable', lang)
        
        model_config = self.models_config.get(model_type, self.models_config['chat'])
        model = model_config['model']
        timeout = model_config.get('timeout', 60)
        
        if not model:
            template = self._('Model for {model_type} not configured', lang)
            return template.format(model_type=model_type)
        
        try:
            payload = {
                'model': model,
                'messages': messages,
                'stream': stream,
                'options': {
                    'num_ctx': model_config['context'],
                    'temperature': model_config['temperature'],
                    'top_p': model_config['top_p'],
                    'stop': ['<|im_end|>', '<|endoftext|>', '\n\n\n'],
                }
            }
            
            self.logger.info(f"Sending request to Ollama. Model: {model}, timeout: {timeout}s")
            
            response = requests.post(
                f"{self.ollama_url}/api/chat",
                json=payload,
                timeout=timeout
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result['message']['content']
                
                for stop_token in ['<|endoftext|>', '<|im_end|>']:
                    if stop_token in content:
                        content = content[:content.index(stop_token)]
                
                if model_type == 'chat' and model_config['temperature'] < 0.3:
                    content = content.split('\n')[0].strip()
                
                return content.strip()
            else:
                error_msg = f"Ollama error: {response.status_code}"
                self.logger.error(error_msg)
                return f"{self._('Error', lang)}: {response.status_code}"
                
        except requests.exceptions.Timeout:
            self.logger.error(f"Timeout ({timeout}s) when calling Ollama. Model: {model}")
            template = self._('Timeout ({timeout}s) when calling the model. Try increasing timeout in .env or simplify your request.', lang)
            return template.format(timeout=timeout)
        except requests.exceptions.ConnectionError:
            self.logger.error(f"Connection error to Ollama at {self.ollama_url}")
            return self._('Could not connect to Ollama', lang)
        except Exception as e:
            self.logger.error(f"Error calling Ollama: {str(e)}")
            return f"{self._('Error', lang)}: {str(e)}"
    
    # --- Context handling methods ---
    def _estimate_tokens(self, text):
        """Rough token estimation using configured characters per token."""
        return len(text) // self.token_chars + 1
    
    def _build_context_prompt(self, history, lang='ru'):
        """
        Format conversation history into a string for inclusion in the prompt.
        Only text is used; timestamps are omitted unless needed.
        """
        if not history:
            return ""
        lines = []
        for msg in history:
            role = self._("User", lang) if msg['role'] == 'user' else self._("Assistant", lang)
            lines.append(f"{role}: {msg['content']}")
        return "\n".join(lines)
    
    def _get_context_for_model(self, session_id, model_type, current_query, lang='ru'):
        """
        Retrieve and prune conversation history to fit within CONTEXT_HISTORY_PERCENT% of the model's context window.
        Returns a formatted history string.
        """
        if not session_id:
            return ""
        
        model_config = self.models_config.get(model_type, self.models_config['chat'])
        max_context_tokens = model_config['context']
        # Reserve configured percentage of the window for history + current query
        available_tokens = int(max_context_tokens * (self.context_history_percent / 100.0))
        
        # Estimate tokens for the current query (including prompt overhead)
        # We'll be conservative: assume the prompt template adds some tokens.
        # For simplicity, we subtract a fixed overhead (e.g., 500 tokens) for the rest of the prompt.
        overhead = 500
        query_tokens = self._estimate_tokens(current_query)
        remaining_for_history = available_tokens - query_tokens - overhead
        if remaining_for_history <= 0:
            return ""
        
        # Fetch history limited by token count
        history_msgs = get_session_text_history(session_id, remaining_for_history)
        return self._build_context_prompt(history_msgs, lang)
    
    # --- Existing methods with context added ---
    def process_message(self, message_text, current_time_str, lang='ru', session_id=None):
        """Process text message through router model, including conversation history."""
        response_language = 'Russian' if lang == 'ru' else 'English'
        
        # Retrieve context if session_id is provided
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
            {
                'role': 'system',
                'content': 'You are a request router. Answer ONLY with one line in the specified language. No explanations.'
            },
            {'role': 'user', 'content': prompt}
        ]
        
        self.logger.info(f"Sending request to router: {message_text}")
        router_response = self.call_ollama(router_messages, model_type='chat', lang=lang)
        self.logger.info(f"Router response: {router_response}")
        
        return self._parse_router_response(router_response, message_text, current_time_str, lang)
    
    def _parse_router_response(self, response, original_query, current_time_str, lang='ru'):
        """Parse router response"""
        response = response.strip()
        
        markers = {
            '[-IMAGE-]': 'image',
            '[-CAMERA-]': 'camera',
            '[-REASONING-]': 'reasoning',
            '[-RAG-]': 'rag'  # new marker for document search
        }
        
        for marker, action in markers.items():
            if marker in response:
                parts = response.split(marker, 1)
                processed = parts[1].strip() if len(parts) > 1 else ""
                
                if action == 'reasoning':
                    return {
                        'action': action,
                        'query': processed,
                        'needs_reasoning': True
                    }
                else:
                    return {
                        'action': action,
                        'query': processed,
                        'needs_reasoning': False
                    }
        
        return {
            'action': 'none',
            'query': response,
            'needs_reasoning': False
        }
    
    def process_reasoning(self, query, current_time_str, lang='ru', session_id=None):
        """Process complex query via reasoning model, including conversation history."""
        response_language = 'Russian' if lang == 'ru' else 'English'
        
        # Retrieve context
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
        response = self.call_ollama(
            [{'role': 'user', 'content': reasoning_prompt}],
            model_type='reasoning',
            lang=lang
        )
        self.logger.info(f"Reasoning model response: {response[:100]}...")
        
        return response