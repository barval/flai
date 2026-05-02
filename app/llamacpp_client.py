# app/llamacpp_client.py
"""
Client for llama-server/llama-swap OpenAI-compatible API.

Uses backend pattern to support:
- DirectLlamaBackend: direct connection to llama-server
- LlamaSwapBackend: connection via llama-swap proxy
"""

import logging
import os
import requests
from typing import Dict, List, Optional, Any, Union

from flask import current_app
from flask_babel import gettext as _
from flask_babel import force_locale

from app.model_config import get_model_config
from app.utils import estimate_tokens
from app.circuit_breaker import CircuitBreaker, CircuitBreakerOpen


class AbstractLlamaBackend:
    """Abstract backend for LLM inference."""

    def __init__(self, app=None):
        self.app = app
        self.logger = logging.getLogger(__name__)

    def get_base_url(self) -> str:
        raise NotImplementedError

    def check_availability(self) -> bool:
        raise NotImplementedError

    def chat(self, messages: List[Dict], model: str, config: Dict, timeout: int, lang: str, model_type: str = 'chat') -> str:
        raise NotImplementedError

    def get_embeddings(self, texts: List[str], model: str, config: Dict, timeout: int) -> Optional[List[List[float]]]:
        raise NotImplementedError

    def unload_all_models(self) -> bool:
        raise NotImplementedError

    def get_running_models(self) -> List[str]:
        raise NotImplementedError


class DirectLlamaBackend(AbstractLlamaBackend):
    """Direct connection to llama-server."""

    def __init__(self, app=None):
        super().__init__(app)
        self.circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=60)

    def get_base_url(self) -> str:
        url = self.app.config.get('LLAMACPP_URL') if self.app else None
        if not url:
            url = 'http://flai-llamacpp:8033'
        return url.rstrip('/')

    def check_availability(self) -> bool:
        try:
            response = requests.get(f"{self.get_base_url()}/v1/models", timeout=5)
            return response.status_code == 200
        except Exception:
            return False

    def chat(self, messages: List[Dict], model: str, config: Dict, timeout: int, lang: str, model_type: str = 'chat') -> str:
        base_url = self.get_base_url()
        context = config.get('context_length', 4096)
        temperature = config.get('temperature', 0.7)
        top_p = config.get('top_p', 0.9)

        payload = {
            'model': model,
            'messages': messages,
            'stream': False,
            'max_tokens': context,
            'temperature': temperature,
            'top_p': top_p,
            'stop': ['</s>', '<|eot_id|>'],
        }

        if not self.circuit_breaker.can_execute():
            return 'Service temporarily unavailable. Circuit breaker is open.'

        try:
            response = requests.post(f"{base_url}/v1/chat/completions", json=payload, timeout=timeout)
            if response.status_code == 200:
                result = response.json()
                choices = result.get('choices', [])
                if not choices:
                    return 'Model returned empty response'

                content = choices[0].get('message', {}).get('content', '')
                if content is None:
                    return 'Model returned empty response'

                for stop_token in ['</s>', '<|eot_id|>']:
                    if stop_token in content:
                        content = content[:content.index(stop_token)]

                self.circuit_breaker.record_success()
                return content.strip()
            else:
                self.circuit_breaker.record_failure()
                return f'Error: {response.status_code}'
        except requests.exceptions.Timeout:
            self.circuit_breaker.record_failure()
            return f'Timeout ({timeout}s) calling model'
        except requests.exceptions.ConnectionError:
            self.circuit_breaker.record_failure()
            return 'Could not connect to llama-server'
        except Exception as e:
            self.logger.error(f'Error: {e}')
            return f'Error: {str(e)}'

    def get_embeddings(self, texts: List[str], model: str, config: Dict, timeout: int) -> Optional[List[List[float]]]:
        base_url = self.get_base_url()
        payload = {'model': model, 'input': texts}
        try:
            response = requests.post(f"{base_url}/v1/embeddings", json=payload, timeout=timeout)
            if response.status_code == 200:
                result = response.json()
                data = result.get('data', [])
                data.sort(key=lambda x: x.get('index', 0))
                return [item['embedding'] for item in data]
            return None
        except Exception as e:
            self.logger.error(f'Embedding error: {e}')
            return None

    def unload_all_models(self) -> bool:
        self.logger.info('Direct backend: unload called')
        return True

    def get_running_models(self) -> List[str]:
        return []


class LlamaSwapBackend(AbstractLlamaBackend):
    """Connection via llama-swap proxy."""

    def __init__(self, app=None):
        super().__init__(app)
        self.circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=60)

    def get_base_url(self) -> str:
        url = os.getenv('LLAMA_SWAP_URL')
        if not url:
            url = 'http://flai-llamaswap:8080'
        return url.rstrip('/')

    def check_availability(self) -> bool:
        try:
            response = requests.get(f"{self.get_base_url()}/health", timeout=5)
            return response.status_code == 200
        except Exception:
            return False

    def chat(self, messages: List[Dict], model: str, config: Dict, timeout: int, lang: str, model_type: str = 'chat') -> str:
        base_url = self.get_base_url()
        temperature = config.get('temperature', 0.7)
        top_p = config.get('top_p', 0.9)

        model_name = model_type

        payload = {
            'model': model_name,
            'messages': messages,
            'stream': False,
            'temperature': temperature,
            'top_p': top_p,
            'stop': ['</s>', '<|eot_id|>'],
        }

        self.logger.info(f"LlamaSwapBackend request: model={model}, payload keys={list(payload.keys())}")

        if not self.circuit_breaker.can_execute():
            return 'Service temporarily unavailable. Circuit breaker is open.'

        try:
            response = requests.post(f"{base_url}/v1/chat/completions", json=payload, timeout=timeout)
            self.logger.info(f"LlamaSwapBackend response: {response.status_code}")
            if response.status_code == 200:
                result = response.json()
                choices = result.get('choices', [])
                if not choices:
                    return 'Model returned empty response'

                content = choices[0].get('message', {}).get('content', '')
                if content is None:
                    return 'Model returned empty response'

                for stop_token in ['</s>', '<|eot_id|>']:
                    if stop_token in content:
                        content = content[:content.index(stop_token)]

                self.circuit_breaker.record_success()
                return content.strip()
            else:
                self.circuit_breaker.record_failure()
                return f'Error: {response.status_code}'
        except requests.exceptions.Timeout:
            self.circuit_breaker.record_failure()
            return f'Timeout ({timeout}s) calling model'
        except requests.exceptions.ConnectionError:
            self.circuit_breaker.record_failure()
            return 'Could not connect to llama-swap'
        except Exception as e:
            self.logger.error(f'Error: {e}')
            return f'Error: {str(e)}'

    def get_embeddings(self, texts: List[str], model: str, config: Dict, timeout: int) -> Optional[List[List[float]]]:
        base_url = self.get_base_url()
        payload = {'model': model, 'input': texts}
        try:
            response = requests.post(f"{base_url}/v1/embeddings", json=payload, timeout=timeout)
            if response.status_code == 200:
                result = response.json()
                data = result.get('data', [])
                data.sort(key=lambda x: x.get('index', 0))
                return [item['embedding'] for item in data]
            return None
        except Exception as e:
            self.logger.error(f'Embedding error: {e}')
            return None

    def unload_all_models(self) -> bool:
        base_url = self.get_base_url()
        try:
            response = requests.post(f"{base_url}/api/models/unload", timeout=30)
            if response.status_code == 200:
                self.logger.info('llama-swap: models unloaded')
                return True
            return False
        except Exception as e:
            self.logger.error(f'Unload error: {e}')
            return False

    def get_running_models(self) -> List[str]:
        base_url = self.get_base_url()
        try:
            response = requests.get(f"{base_url}/running", timeout=10)
            if response.status_code == 200:
                return response.json().get('models', [])
            return []
        except Exception:
            return []


class LlamaCppClient:
    """Client for llama-server/llama-swap via backend pattern."""

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.available = False
        self.app = app

        backend_type = os.getenv('LLAMACP_BACKEND', 'llamacpp')

        if backend_type == 'llama-swap':
            self.backend = LlamaSwapBackend(app)
            self.logger.info('Using LlamaSwapBackend')
        else:
            self.backend = DirectLlamaBackend(app)
            self.logger.info('Using DirectLlamaBackend')

        if app:
            self.init_app(app)

    def init_app(self, app):
        self.app = app
        self.check_availability()

    def check_availability(self) -> bool:
        self.available = self.backend.check_availability()
        if self.available:
            self.logger.info(f'LLM backend available at {self.backend.get_base_url()}')
        else:
            self.logger.warning(f'LLM backend unavailable at {self.backend.get_base_url()}')
        return self.available

    def _translate(self, key: str, lang: str = 'ru', **kwargs) -> str:
        with current_app.app_context():
            with force_locale(lang):
                return _(key, **kwargs)

    def _validate_prompt(self, messages: List[Dict], model_type: str, lang: str) -> Optional[str]:
        config = get_model_config(model_type)
        if not config:
            return None

        max_context = config.get('context_length', 32768)
        hard_limit = int(max_context * 0.95)

        total_tokens = 0
        for msg in messages:
            content = msg.get('content', '')
            if isinstance(content, str):
                total_tokens += estimate_tokens(content, model_type, lang)
            elif isinstance(content, list):
                for part in content:
                    if part.get('type') == 'text':
                        total_tokens += estimate_tokens(part.get('text', ''), model_type, lang)
                    elif part.get('type') == 'image_url':
                        total_tokens += 1000

        if total_tokens > hard_limit:
            return f'Request too long ({total_tokens} tokens, limit {hard_limit})'
        return None

    def chat(self, messages: List[Dict], model_type: str = 'chat', lang: str = 'ru', validate: bool = True) -> str:
        if validate:
            error = self._validate_prompt(messages, model_type, lang)
            if error:
                return error

        config = get_model_config(model_type)
        if not config:
            return 'Model configuration missing'

        model = config.get('model_name')
        if not model:
            return f'Model for {model_type} not configured'

        timeout = config.get('timeout', 300)
        return self.backend.chat(messages, model, config, timeout, lang, model_type=model_type)

    def chat_with_image(self, text: str, image_base64: str, model_type: str = 'multimodal', lang: str = 'ru') -> str:
        if image_base64.startswith('data:'):
            image_content = image_base64
        else:
            image_content = f'data:image/jpeg;base64,{image_base64}'

        messages = [{
            'role': 'user',
            'content': [
                {'type': 'text', 'text': text},
                {'type': 'image_url', 'image_url': {'url': image_content}}
            ]
        }]
        return self.chat(messages, model_type=model_type, lang=lang)

    def get_embeddings(self, texts: List[str], model_type: str = 'embedding', lang: str = 'ru') -> Optional[List[List[float]]]:
        config = get_model_config(model_type)
        if not config:
            return None

        # Use model_type (module name like 'embedding') as model identifier for llama-swap
        # This maps to the 'id' field in llama-swap config
        model = model_type
        if not model:
            return None

        timeout = config.get('timeout', 120)
        return self.backend.get_embeddings(texts, model, config, timeout)

    def call(self, messages: List[Dict], model_type: str = 'chat', stream: bool = False, lang: str = 'ru', validate: bool = True) -> Union[str, Dict[str, Any]]:
        return self.chat(messages, model_type=model_type, lang=lang, validate=validate)

    def unload_all_models(self) -> bool:
        return self.backend.unload_all_models()

    def get_running_models(self) -> List[str]:
        return self.backend.get_running_models()