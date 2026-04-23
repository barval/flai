# app/llamacpp_client.py
"""
Client for llama-server (llama.cpp) OpenAI-compatible API.

Communicates via OpenAI-compatible endpoints:
  - POST /v1/chat/completions  (chat, reasoning, multimodal)
  - POST /v1/embeddings        (embedding)
  - GET  /v1/models             (list available models)

The llama-server is started in router mode (--model-dir) to support
dynamic model switching without restart.
"""

import logging
import threading
import requests
import traceback
import base64
from typing import Dict, List, Optional, Any, Union

from flask import current_app
from flask_babel import gettext as _
from flask_babel import force_locale

from app.model_config import get_model_config
from app.utils import estimate_tokens
from app.circuit_breaker import CircuitBreaker, CircuitBreakerOpen

# llama.cpp runs with --models-max 1, so only one model can be in VRAM at a time.
# When different model types are requested concurrently (embedding vs multimodal),
# we serialize them to avoid constant model reloading thrashing.
# Chat/reasoning use the same model so they don't conflict with each other.

_model_switch_lock = threading.Lock()


class LlamaCppClient:
    """Client for llama-server OpenAI-compatible API."""

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.available = False
        self.app = app
        # Circuit breaker: 3 failures → open for 60s
        self.circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
        if app:
            self.init_app(app)

    def init_app(self, app):
        """Initialize with Flask app config."""
        self.check_availability()

    def _get_service_url(self, module_type: str) -> Optional[str]:
        """Get the service URL for a given module type.
        Priority: 1) service_url from DB,
                  2) LLAMACPP_URL from config (global fallback).
        """
        config = get_model_config(module_type)
        if config:
            service_url = config.get('service_url')
            if service_url:
                return service_url.rstrip('/')
        # Global fallback from .env
        if self.app and self.app.config.get('LLAMACPP_URL'):
            return self.app.config['LLAMACPP_URL'].rstrip('/')
        # Last resort default
        return 'http://flai-llamacpp:8033'

    def check_availability(self) -> bool:
        """Check if llama-server is reachable via /v1/models endpoint."""
        url = self._get_service_url('chat')
        if not url:
            # No URL configured — assume available
            self.logger.warning("No llama-server URL configured, assuming available")
            self.available = True
            return True

        try:
            response = requests.get(f"{url}/v1/models", timeout=5)
            if response.status_code == 200:
                self.available = True
                self.logger.info(f"llama-server is available at {url}")
                return True
            else:
                self.logger.warning(
                    f"llama-server returned status {response.status_code} at {url}"
                )
                self.available = False
                return False
        except requests.exceptions.ConnectionError:
            self.logger.error(
                f"Cannot connect to llama-server at {url} - service may not be running"
            )
            self.available = False
            return False
        except requests.exceptions.Timeout:
            self.logger.error(f"Timeout connecting to llama-server at {url}")
            self.available = False
            return False
        except Exception as e:
            self.logger.error(f"Error checking llama-server availability: {e}")
            self.available = False
            return False

    def _translate(self, key: str, lang: str = 'ru', **kwargs) -> str:
        """Translate a message using Flask-Babel."""
        with current_app.app_context():
            with force_locale(lang):
                return _(key, **kwargs)

    def _validate_prompt(
        self,
        messages: List[Dict[str, Any]],
        model_type: str,
        lang: str,
    ) -> Optional[str]:
        """
        Validate that the total prompt fits into the model's context window.
        Returns error message if validation fails, None otherwise.
        """
        config = get_model_config(model_type)
        if not config:
            self.logger.warning(
                f"Missing model config for {model_type}, skipping validation"
            )
            return None

        max_context = config.get('context_length', 32768)
        hard_limit = int(max_context * 0.95)

        total_tokens = 0
        for msg in messages:
            content = msg.get('content', '')
            if isinstance(content, str):
                total_tokens += estimate_tokens(content, model_type, lang)
            elif isinstance(content, list):
                # Multimodal: count text parts only, estimate images
                for part in content:
                    if part.get('type') == 'text':
                        total_tokens += estimate_tokens(
                            part.get('text', ''), model_type, lang
                        )
                    elif part.get('type') == 'image_url':
                        total_tokens += 1000  # rough estimate per image

        if total_tokens > hard_limit:
            error_msg = self._translate(
                'Request too long, please simplify your request', lang
            )
            self.logger.error(
                f"Prompt validation failed: {total_tokens} tokens "
                f"(limit {hard_limit}) for {model_type}"
            )
            return error_msg

        self.logger.info(
            f"Prompt validation passed: {total_tokens}/{hard_limit} tokens "
            f"({total_tokens / max_context * 100:.1f}%)"
        )
        return None

    def chat(
        self,
        messages: List[Dict[str, Any]],
        model_type: str = 'chat',
        lang: str = 'ru',
        validate: bool = True,
    ) -> str:
        """
        Call llama-server chat completion via OpenAI-compatible API.
        Returns content string on success, error message on failure.

        Args:
            messages: List of message dicts in OpenAI format
            model_type: Module type (chat, reasoning, multimodal)
            lang: Language for error messages
            validate: Whether to validate prompt size before sending
        """
        # Optional validation
        if validate:
            error = self._validate_prompt(messages, model_type, lang)
            if error:
                return error

        config = get_model_config(model_type)
        if not config:
            return self._translate('Model configuration missing', lang)

        model = config.get('model_name')
        if not model:
            return self._translate(
                'Model for {model_type} not configured', lang
            ).format(model_type=model_type)

        service_url = self._get_service_url(model_type)
        if not service_url:
            service_url = 'http://llamacpp:8080'
            self.logger.warning(
                f"No service_url for {model_type}, using default {service_url}"
            )

        timeout = config.get('timeout', 300)
        context = config.get('context_length', 4096)
        temperature = config.get('temperature', 0.7)
        top_p = config.get('top_p', 0.9)

        # OpenAI-compatible payload
        payload = {
            'model': model,
            'messages': messages,
            'stream': False,
            'max_tokens': context,
            'temperature': temperature,
            'top_p': top_p,
            'stop': ['</s>', '<|eot_id|>'],
        }

        self.logger.info(
            f"Sending request to {service_url}, model={model}, timeout={timeout}s"
        )

        # Circuit breaker check — fail fast if service is down
        if not self.circuit_breaker.can_execute():
            cb_state = self.circuit_breaker.get_state()
            self.logger.warning(
                f"Circuit breaker OPEN for llama-server: "
                f"failures={cb_state['failure_count']}/{cb_state['failure_threshold']}"
            )
            return self._translate(
                'Service temporarily unavailable. Circuit breaker is open after repeated failures.',
                lang
            )

        try:
            response = requests.post(
                f"{service_url}/v1/chat/completions",
                json=payload,
                timeout=timeout
            )
            if response.status_code == 200:
                result = response.json()
                choices = result.get('choices', [])
                if not choices:
                    self.logger.error(f"llama-server returned no choices: {result}")
                    return self._translate('Model returned empty response', lang)

                content = choices[0].get('message', {}).get('content', '')
                if content is None:
                    self.logger.error(f"llama-server returned None content: {result}")
                    return self._translate('Model returned empty response', lang)

                # Remove stop tokens
                for stop_token in ['</s>', '<|eot_id|>']:
                    if stop_token in content:
                        content = content[:content.index(stop_token)]

                # For chat models with low temperature, keep only first line
                if model_type == 'chat' and temperature < 0.3:
                    content = content.split('\n')[0].strip()

                self.circuit_breaker.record_success()
                return content.strip()
            else:
                self.circuit_breaker.record_failure()
                self.logger.error(
                    f"llama-server error: {response.status_code} - {response.text}"
                )
                return f"{self._translate('Error', lang)}: {response.status_code}"
        except requests.exceptions.Timeout:
            self.circuit_breaker.record_failure()
            self.logger.error(
                f"Timeout ({timeout}s) calling {model} at {service_url}"
            )
            template = self._translate(
                'Timeout ({timeout}s) when calling the model. '
                'Try increasing timeout in admin panel or simplify your request.',
                lang
            )
            return template.format(timeout=timeout)
        except requests.exceptions.ConnectionError:
            self.circuit_breaker.record_failure()
            self.logger.error(f"Connection error to llama-server at {service_url}")
            return self._translate('Could not connect to llama-server', lang)
        except Exception as e:
            self.logger.error(
                f"Error calling llama-server: {e}\n{traceback.format_exc()}"
            )
            return f"{self._translate('Error', lang)}: {str(e)}"

    def chat_with_image(
        self,
        text: str,
        image_base64: str,
        model_type: str = 'multimodal',
        lang: str = 'ru',
    ) -> str:
        """
        Call llama-server with image + text (multimodal).
        Uses OpenAI-compatible format with image_url in messages.
        The llama.cpp router automatically handles mmproj files
        when models are placed in subdirectories with mmproj*.gguf.
        """
        # Detect mime type from base64 header or default to jpeg
        if image_base64.startswith('data:'):
            image_content = image_base64
        else:
            image_content = f"data:image/jpeg;base64,{image_base64}"

        messages = [{
            'role': 'user',
            'content': [
                {'type': 'text', 'text': text},
                {'type': 'image_url', 'image_url': {'url': image_content}}
            ]
        }]

        return self.chat(messages, model_type=model_type, lang=lang)

    def get_embeddings(
        self,
        texts: List[str],
        model_type: str = 'embedding',
        lang: str = 'ru',
    ) -> Optional[List[List[float]]]:
        """
        Get embeddings via OpenAI-compatible /v1/embeddings endpoint.
        """
        config = get_model_config(model_type)
        if not config:
            self.logger.error(f"No model config for {model_type}")
            return None

        model = config.get('model_name')
        if not model:
            self.logger.error(f"No model name configured for {model_type}")
            return None

        service_url = self._get_service_url(model_type)
        if not service_url:
            service_url = 'http://llamacpp:8080'
            self.logger.warning(
                f"No service_url for {model_type}, using default {service_url}"
            )

        timeout = config.get('timeout', 120)

        payload = {
            'model': model,
            'input': texts,
        }

        self.logger.info(
            f"Sending embedding request to {service_url}, model={model}, "
            f"texts={len(texts)}, timeout={timeout}s"
        )
        try:
            response = requests.post(
                f"{service_url}/v1/embeddings",
                json=payload,
                timeout=timeout
            )
            if response.status_code == 200:
                result = response.json()
                data = result.get('data', [])
                # Sort by index (OpenAI spec requires ordering by index)
                data.sort(key=lambda x: x.get('index', 0))
                embeddings = [item['embedding'] for item in data]
                self.logger.info(
                    f"Embedding successful: {len(embeddings)} vectors, "
                    f"dim={len(embeddings[0]) if embeddings else 0}"
                )
                return embeddings
            else:
                self.logger.error(
                    f"Embedding error: {response.status_code} - {response.text}"
                )
                return None
        except requests.exceptions.Timeout:
            self.logger.error(f"Timeout calling embedding endpoint at {service_url}")
            return None
        except requests.exceptions.ConnectionError:
            self.logger.error(f"Connection error to llama-server at {service_url}")
            return None
        except Exception as e:
            self.logger.error(f"Error getting embeddings: {e}")
            return None

    def call(
        self,
        messages: List[Dict[str, Any]],
        model_type: str = 'chat',
        stream: bool = False,
        lang: str = 'ru',
        validate: bool = True,
    ) -> Union[str, Dict[str, Any]]:
        """
        Compatibility wrapper matching the LlamaCppClient.call() signature.
        Note: stream is ignored — llama-server streaming would require
        a separate implementation with SSE parsing.
        """
        return self.chat(messages, model_type=model_type, lang=lang, validate=validate)
