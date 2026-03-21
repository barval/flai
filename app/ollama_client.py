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
        self.ollama_url = None
        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize with Flask app config."""
        self.ollama_url = app.config.get('OLLAMA_URL')
        self.check_availability()

    def check_availability(self) -> bool:
        """Check if Ollama service is reachable."""
        if not self.ollama_url:
            self.logger.error("OLLAMA_URL not configured")
            return False
        try:
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if response.status_code == 200:
                self.available = True
                return True
        except Exception as e:
            self.logger.error(f"Error connecting to Ollama: {e}")
        self.available = False
        return False

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
        if not self.available and not self.check_availability():
            return self._translate('Ollama service unavailable', lang)

        config = self._get_model_config(model_type)
        if not config:
            return self._translate('Model configuration missing', lang)

        model = config.get('model_name')
        if not model:
            return self._translate('Model for {model_type} not configured', lang).format(model_type=model_type)

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

        self.logger.info(f"Sending request to Ollama: model={model}, timeout={timeout}s")
        try:
            response = requests.post(
                f"{self.ollama_url}/api/chat",
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
            self.logger.error(f"Timeout ({timeout}s) calling {model}")
            template = self._translate(
                'Timeout ({timeout}s) when calling the model. Try increasing timeout in admin panel or simplify your request.',
                lang
            )
            return template.format(timeout=timeout)
        except requests.exceptions.ConnectionError:
            self.logger.error(f"Connection error to {self.ollama_url}")
            return self._translate('Could not connect to Ollama', lang)
        except Exception as e:
            self.logger.error(f"Error calling Ollama: {e}\n{traceback.format_exc()}")
            return f"{self._translate('Error', lang)}: {str(e)}"