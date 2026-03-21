# app/ollama_client.py
import logging
import requests
import traceback
from typing import Dict, List, Optional, Any, Union

from flask import current_app
from flask_babel import gettext as _
from flask_babel import force_locale

from app.model_config import get_model_config


class OllamaClient:
    """Centralized client for Ollama API calls."""

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.available = False
        # No global ollama_url anymore
        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize with Flask app config."""
        # No global URL, we'll get from model config each time
        # But we still check availability? We'll check per call.
        self.check_availability()  # might be removed, but keep for backward compat

    def check_availability(self) -> bool:
        """Check if Ollama service is reachable (deprecated, but kept)."""
        # We can't check without URL, so just return True if config exists.
        # Better to remove this method later.
        self.available = True
        return True

    def _get_model_config(self, model_type: str) -> Optional[Dict[str, Any]]:
        """Retrieve model configuration from database."""
        return get_model_config(model_type)

    def _translate(self, key: str, lang: str = 'ru', **kwargs) -> str:
        """Translate a message using Flask-Babel."""
        with current_app.app_context():
            with force_locale(lang):
                return _(key, **kwargs)

    def call(
        self,
        messages: List[Dict[str, Any]],
        model_type: str = 'chat',
        stream: bool = False,
        lang: str = 'ru',
    ) -> Union[str, Dict[str, Any]]:
        """
        Call Ollama chat completion.
        Returns content string on success, error message on failure.
        """
        config = self._get_model_config(model_type)
        if not config:
            return self._translate('Model configuration missing', lang)

        model = config.get('model_name')
        if not model:
            return self._translate('Model for {model_type} not configured', lang).format(model_type=model_type)

        # Get URL from config, fallback to default
        ollama_url = config.get('ollama_url')
        if not ollama_url:
            ollama_url = 'http://ollama:11434'   # default if not set
            self.logger.warning(f"No ollama_url for {model_type}, using default {ollama_url}")

        timeout = config.get('timeout', 60)
        context = config.get('context_length', 32768)
        temperature = config.get('temperature', 0.7)
        top_p = config.get('top_p', 0.9)

        payload = {
            'model': model,
            'messages': messages,
            'stream': stream,
            'options': {
                'num_ctx': context,
                'temperature': temperature,
                'top_p': top_p,
                'stop': ['<|im_end|>', '<|endoftext|>', '\n\n\n'],
            }
        }

        self.logger.info(f"Sending request to {ollama_url}, model={model}, timeout={timeout}s")
        try:
            response = requests.post(
                f"{ollama_url}/api/chat",
                json=payload,
                timeout=timeout
            )
            if response.status_code == 200:
                result = response.json()
                content = result['message']['content']
                if content is None:
                    self.logger.error(f"Ollama returned None content: {result}")
                    return self._translate('Model returned empty response', lang)

                # Remove stop tokens
                for stop_token in ['<|endoftext|>', '<|im_end|>']:
                    if stop_token in content:
                        content = content[:content.index(stop_token)]

                # For chat models with low temperature, keep only first line
                if model_type == 'chat' and temperature < 0.3:
                    content = content.split('\n')[0].strip()

                return content.strip()
            else:
                self.logger.error(f"Ollama error: {response.status_code} - {response.text}")
                return f"{self._translate('Error', lang)}: {response.status_code}"
        except requests.exceptions.Timeout:
            self.logger.error(f"Timeout ({timeout}s) calling {model} at {ollama_url}")
            template = self._translate(
                'Timeout ({timeout}s) when calling the model. Try increasing timeout in admin panel or simplify your request.',
                lang
            )
            return template.format(timeout=timeout)
        except requests.exceptions.ConnectionError:
            self.logger.error(f"Connection error to {ollama_url}")
            return self._translate('Could not connect to Ollama', lang)
        except Exception as e:
            self.logger.error(f"Error calling Ollama: {e}\n{traceback.format_exc()}")
            return f"{self._translate('Error', lang)}: {str(e)}"