# app/llamacpp_client.py
"""
Client for llama-server/llama-swap OpenAI-compatible API.

Uses backend pattern to support:
- DirectLlamaBackend: direct connection to llama-server
- LlamaSwapBackend: connection via llama-swap proxy
"""

import json
import logging
import os
from collections.abc import Generator
from typing import Any

import requests
from flask import current_app
from flask_babel import force_locale
from flask_babel import gettext as _

from app.circuit_breaker import CircuitBreaker
from app.model_config import get_model_config
from app.utils import estimate_tokens


def _tr(key: str, lang: str = "ru", **kwargs: Any) -> str:
    """Translate a user-facing error string using the specified language."""
    try:
        with force_locale(lang):
            result = _(key)
        if kwargs:
            result = result.format(**kwargs) if kwargs else result
        return result
    except Exception:
        return key.format(**kwargs) if kwargs else key


class AbstractLlamaBackend:
    """Abstract backend for LLM inference."""

    def __init__(self, app=None):
        self.app = app
        self.logger = logging.getLogger(__name__)

    def get_base_url(self) -> str:
        raise NotImplementedError

    def check_availability(self) -> bool:
        raise NotImplementedError

    def chat(
        self, messages: list[dict], model: str, config: dict, timeout: int, lang: str, model_type: str = "chat"
    ) -> str:
        raise NotImplementedError

    def chat_stream(
        self, messages: list[dict], model: str, config: dict, timeout: int, lang: str, model_type: str = "chat"
    ) -> Generator[str, None, None]:
        raise NotImplementedError

    def get_embeddings(self, texts: list[str], model: str, config: dict, timeout: int) -> list[list[float]] | None:
        raise NotImplementedError

    def unload_all_models(self) -> bool:
        raise NotImplementedError

    def get_running_models(self) -> list[str]:
        raise NotImplementedError


class DirectLlamaBackend(AbstractLlamaBackend):
    """Direct connection to llama-server."""

    def __init__(self, app=None):
        super().__init__(app)
        self.circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=60)

    def get_base_url(self) -> str:
        url = self.app.config.get("LLAMACPP_URL") if self.app else None
        if not url:
            url = "http://flai-llamacpp:8033"
        return url.rstrip("/")

    def check_availability(self) -> bool:
        try:
            response = requests.get(f"{self.get_base_url()}/v1/models", timeout=5)
            return response.status_code == 200
        except Exception:
            return False

    def chat(
        self, messages: list[dict], model: str, config: dict, timeout: int, lang: str, model_type: str = "chat"
    ) -> str:
        base_url = self.get_base_url()
        context = config.get("context_length", 4096)
        temperature = config.get("temperature", 0.7)
        top_p = config.get("top_p", 0.9)
        repeat_penalty = config.get("repeat_penalty", 1.1)

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "max_tokens": context,
            "temperature": temperature,
            "top_p": top_p,
            "repeat_penalty": repeat_penalty,
            "stop": ["</s>", "<|eot_id|>"],
        }

        if not self.circuit_breaker.can_execute():
            return _tr("Service temporarily unavailable. Circuit breaker is open after repeated failures.", lang)

        try:
            response = requests.post(f"{base_url}/v1/chat/completions", json=payload, timeout=timeout)
            if response.status_code == 200:
                result = response.json()
                choices = result.get("choices", [])
                if not choices:
                    return _tr("Model returned empty response", lang)

                content = choices[0].get("message", {}).get("content", "")
                if content is None:
                    return _tr("Model returned empty response", lang)

                for stop_token in ["</s>", "<|eot_id|>"]:
                    if stop_token in content:
                        content = content[: content.index(stop_token)]

                self.circuit_breaker.record_success()
                return content.strip()  # type: ignore[no-any-return]
            else:
                self.circuit_breaker.record_failure()
                return _tr("HTTP error {status}", lang, status=response.status_code)
        except requests.exceptions.Timeout:
            self.circuit_breaker.record_failure()
            return _tr(
                "Timeout ({timeout}s) when calling the model. Try increasing timeout in admin panel or simplify your request.",
                lang,
                timeout=timeout,
            )
        except requests.exceptions.ConnectionError:
            self.circuit_breaker.record_failure()
            return _tr("Could not connect to llama-server", lang)
        except Exception as e:
            self.logger.error(f"Error: {e}")
            return _tr("Error", lang) + f": {str(e)}"

    def chat_stream(
        self, messages: list[dict], model: str, config: dict, timeout: int, lang: str, model_type: str = "chat"
    ) -> Generator[str, None, None]:
        base_url = self.get_base_url()
        context = config.get("context_length", 4096)
        temperature = config.get("temperature", 0.7)
        top_p = config.get("top_p", 0.9)
        repeat_penalty = config.get("repeat_penalty", 1.1)

        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "max_tokens": context,
            "temperature": temperature,
            "top_p": top_p,
            "repeat_penalty": repeat_penalty,
            "stop": ["</s>", "<|eot_id|>"],
        }

        if not self.circuit_breaker.can_execute():
            yield _tr("Service temporarily unavailable. Circuit breaker is open after repeated failures.", lang)
            return

        response = None
        try:
            response = requests.post(f"{base_url}/v1/chat/completions", json=payload, timeout=timeout, stream=True)
            if response.status_code != 200:
                self.circuit_breaker.record_failure()
                yield _tr("HTTP error {status}", lang, status=response.status_code)
                return

            self.circuit_breaker.record_success()
            for line in response.iter_lines():
                if not line:
                    continue
                decoded = line.decode("utf-8")
                if not decoded.startswith("data: "):
                    continue
                data_str = decoded[6:]
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except json.JSONDecodeError:
                    continue
        except requests.exceptions.Timeout:
            self.circuit_breaker.record_failure()
            yield _tr(
                "Timeout ({timeout}s) when calling the model. Try increasing timeout in admin panel or simplify your request.",
                lang,
                timeout=timeout,
            )
        except requests.exceptions.ConnectionError:
            self.circuit_breaker.record_failure()
            yield _tr("Could not connect to llama-server", lang)
        except Exception as e:
            self.logger.error(f"Stream error: {e}")
            yield f"{_tr('Error', lang)}: {str(e)}"
        finally:
            if response is not None:
                response.close()

    def get_embeddings(self, texts: list[str], model: str, config: dict, timeout: int) -> list[list[float]] | None:
        base_url = self.get_base_url()
        payload = {"model": model, "input": texts}
        try:
            response = requests.post(f"{base_url}/v1/embeddings", json=payload, timeout=timeout)
            if response.status_code == 200:
                result = response.json()
                data = result.get("data", [])
                data.sort(key=lambda x: x.get("index", 0))
                return [item["embedding"] for item in data]
            return None
        except Exception as e:
            self.logger.error(f"Embedding error: {e}")
            return None

    def unload_all_models(self) -> bool:
        self.logger.info("Direct backend: unload called")
        return True

    def get_running_models(self) -> list[str]:
        return []


class LlamaSwapBackend(AbstractLlamaBackend):
    """Connection via llama-swap proxy."""

    def __init__(self, app=None):
        super().__init__(app)
        self.circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
        self._degraded_models: set[str] = set()

    def _degrade_model_if_needed(self, model_type: str):
        """Degrade model GPU config if circuit breaker opened due to OOM-like errors."""
        if model_type in self._degraded_models:
            return
        try:
            from app.llama_swap_config import LlamaSwapConfigGenerator

            gen = LlamaSwapConfigGenerator(self.app)
            ok = gen.degrade_and_reload(model_type)
            if ok:
                self._degraded_models.add(model_type)
                self.logger.warning(f"{model_type}: degraded GPU config and reloaded llama-swap")
        except Exception as e:
            self.logger.error(f"Failed to degrade {model_type}: {e}")

    def _record_llama_failure(self, model_type: str):
        """Record circuit breaker failure and degrade on circuit open."""
        just_opened = self.circuit_breaker.record_failure()
        if just_opened:
            self._degrade_model_if_needed(model_type)

    def get_base_url(self) -> str:
        url = os.getenv("LLAMA_SWAP_URL")
        if not url:
            url = "http://flai-llamaswap:8080"
        return url.rstrip("/")

    def check_availability(self) -> bool:
        try:
            response = requests.get(f"{self.get_base_url()}/health", timeout=5)
            return response.status_code == 200
        except Exception:
            return False

    def chat(
        self, messages: list[dict], model: str, config: dict, timeout: int, lang: str, model_type: str = "chat"
    ) -> str:
        base_url = self.get_base_url()
        temperature = config.get("temperature", 0.7)
        top_p = config.get("top_p", 0.9)
        repeat_penalty = config.get("repeat_penalty", 1.1)

        model_name = model_type

        payload = {
            "model": model_name,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "top_p": top_p,
            "repeat_penalty": repeat_penalty,
            "stop": ["</s>", "<|eot_id|>"],
        }

        self.logger.info(f"LlamaSwapBackend request: model={model}, payload keys={list(payload.keys())}")

        if not self.circuit_breaker.can_execute():
            self._degrade_model_if_needed(model_type)
            return _tr("Service temporarily unavailable. Circuit breaker is open after repeated failures.", lang)

        try:
            response = requests.post(f"{base_url}/v1/chat/completions", json=payload, timeout=timeout)
            self.logger.info(f"LlamaSwapBackend response: {response.status_code}")
            if response.status_code == 200:
                result = response.json()
                choices = result.get("choices", [])
                if not choices:
                    return _tr("Model returned empty response", lang)

                content = choices[0].get("message", {}).get("content", "")
                if content is None:
                    return _tr("Model returned empty response", lang)

                for stop_token in ["</s>", "<|eot_id|>"]:
                    if stop_token in content:
                        content = content[: content.index(stop_token)]

                self.circuit_breaker.record_success()
                return content.strip()  # type: ignore[no-any-return]
            else:
                self._record_llama_failure(model_type)
                return _tr("HTTP error {status}", lang, status=response.status_code)
        except requests.exceptions.Timeout:
            self._record_llama_failure(model_type)
            return _tr(
                "Timeout ({timeout}s) when calling the model. Try increasing timeout in admin panel or simplify your request.",
                lang,
                timeout=timeout,
            )
        except requests.exceptions.ConnectionError:
            self._record_llama_failure(model_type)
            return _tr("Could not connect to llama-swap", lang)
        except Exception as e:
            self.logger.error(f"Error: {e}")
            return f"{_tr('Error', lang)}: {str(e)}"

    def chat_stream(
        self, messages: list[dict], model: str, config: dict, timeout: int, lang: str, model_type: str = "chat"
    ) -> Generator[str, None, None]:
        base_url = self.get_base_url()
        temperature = config.get("temperature", 0.7)
        top_p = config.get("top_p", 0.9)
        repeat_penalty = config.get("repeat_penalty", 1.1)
        model_name = model_type

        payload = {
            "model": model_name,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "top_p": top_p,
            "repeat_penalty": repeat_penalty,
            "stop": ["</s>", "<|eot_id|>"],
        }

        if not self.circuit_breaker.can_execute():
            self._degrade_model_if_needed(model_type)
            yield _tr("Service temporarily unavailable. Circuit breaker is open after repeated failures.", lang)
            return

        response = None
        try:
            response = requests.post(f"{base_url}/v1/chat/completions", json=payload, timeout=timeout, stream=True)
            if response.status_code != 200:
                self._record_llama_failure(model_type)
                yield _tr("HTTP error {status}", lang, status=response.status_code)
                return

            self.circuit_breaker.record_success()
            for line in response.iter_lines():
                if not line:
                    continue
                decoded = line.decode("utf-8")
                if not decoded.startswith("data: "):
                    continue
                data_str = decoded[6:]
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except json.JSONDecodeError:
                    continue
        except requests.exceptions.Timeout:
            self._record_llama_failure(model_type)
            yield _tr(
                "Timeout ({timeout}s) when calling the model. Try increasing timeout in admin panel or simplify your request.",
                lang,
                timeout=timeout,
            )
        except requests.exceptions.ConnectionError:
            self._record_llama_failure(model_type)
            yield _tr("Could not connect to llama-swap", lang)
        except Exception as e:
            self.logger.error(f"Stream error: {e}")
            yield f"{_tr('Error', lang)}: {str(e)}"
        finally:
            if response is not None:
                response.close()

    def get_embeddings(self, texts: list[str], model: str, config: dict, timeout: int) -> list[list[float]] | None:
        base_url = self.get_base_url()
        payload = {"model": model, "input": texts}
        try:
            response = requests.post(f"{base_url}/v1/embeddings", json=payload, timeout=timeout)
            self.logger.info(f"Embedding status={response.status_code}")
            if response.status_code == 200:
                result = response.json()
                data = result.get("data", [])
                data.sort(key=lambda x: x.get("index", 0))
                return [item["embedding"] for item in data]
            self.logger.error(f"Embedding failed: {response.text[:200]}")
            return None
        except Exception as e:
            self.logger.error(f"Embedding error: {e}")
            return None

    def unload_all_models(self) -> bool:
        base_url = self.get_base_url()
        try:
            response = requests.post(f"{base_url}/api/models/unload", timeout=30)
            if response.status_code == 200:
                self.logger.info("llama-swap: models unloaded")
                return True
            return False
        except Exception as e:
            self.logger.error(f"Unload error: {e}")
            return False

    def get_running_models(self) -> list[str]:
        base_url = self.get_base_url()
        try:
            response = requests.get(f"{base_url}/running", timeout=10)
            if response.status_code == 200:
                return response.json().get("models", [])  # type: ignore[no-any-return]
            return []
        except Exception:
            return []


class LlamaCppClient:
    """Client for llama-server/llama-swap via backend pattern."""

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.available = False
        self.app = app

        backend_type = os.getenv("LLAMACP_BACKEND", "llamacpp")

        if backend_type == "llama-swap":
            self.backend = LlamaSwapBackend(app)
            self.logger.info("Using LlamaSwapBackend")
        else:
            self.backend = DirectLlamaBackend(app)
            self.logger.info("Using DirectLlamaBackend")

        if app:
            self.init_app(app)

    def init_app(self, app):
        self.app = app
        self.check_availability()

    def check_availability(self) -> bool:
        self.available = self.backend.check_availability()
        if self.available:
            self.logger.info(f"LLM backend available at {self.backend.get_base_url()}")
        else:
            self.logger.warning(f"LLM backend unavailable at {self.backend.get_base_url()}")
        return self.available  # type: ignore[no-any-return]

    def _translate(self, key: str, lang: str = "ru", **kwargs) -> str:
        with current_app.app_context(), force_locale(lang):
            return _(key, **kwargs)  # type: ignore[no-any-return]

    def _validate_prompt(self, messages: list[dict], model_type: str, lang: str) -> str | None:
        config = get_model_config(model_type)
        if not config:
            return None

        max_context = config.get("context_length", 32768)
        hard_limit = int(max_context * 0.95)

        total_tokens = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_tokens += estimate_tokens(content, model_type, lang)
            elif isinstance(content, list):
                for part in content:
                    if part.get("type") == "text":
                        total_tokens += estimate_tokens(part.get("text", ""), model_type, lang)
                    elif part.get("type") == "image_url":
                        total_tokens += 1000

        if total_tokens > hard_limit:
            return self._translate("Request too long, please simplify your request", lang)
        return None

    def chat(self, messages: list[dict], model_type: str = "chat", lang: str = "ru", validate: bool = True) -> str:
        if validate:
            error = self._validate_prompt(messages, model_type, lang)
            if error:
                return error

        config = get_model_config(model_type)
        if not config:
            return self._translate("Model configuration missing", lang)

        model = config.get("model_name")
        if not model:
            return self._translate("Model for {model_type} not configured", lang, model_type=model_type)

        timeout = config.get("timeout", 300)
        return self.backend.chat(messages, model, config, timeout, lang, model_type=model_type)  # type: ignore[no-any-return]

    def chat_stream(
        self, messages: list[dict], model_type: str = "chat", lang: str = "ru", validate: bool = True
    ) -> Generator[str, None, None]:
        if validate:
            error = self._validate_prompt(messages, model_type, lang)
            if error:
                yield error
                return

        config = get_model_config(model_type)
        if not config:
            yield self._translate("Model configuration missing", lang)
            return

        model = config.get("model_name")
        if not model:
            yield self._translate("Model for {model_type} not configured", lang, model_type=model_type)
            return

        timeout = config.get("timeout", 600)
        yield from self.backend.chat_stream(messages, model, config, timeout, lang, model_type=model_type)

    def chat_with_image(self, text: str, image_base64: str, model_type: str = "multimodal", lang: str = "ru") -> str:
        image_content = image_base64 if image_base64.startswith("data:") else f"data:image/jpeg;base64,{image_base64}"

        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": image_content}}],
            }
        ]
        return self.chat(messages, model_type=model_type, lang=lang)

    def chat_with_image_stream(
        self, text: str, image_base64: str, model_type: str = "multimodal", lang: str = "ru"
    ) -> Generator[str, None, None]:
        """Streaming variant of chat_with_image."""
        image_content = image_base64 if image_base64.startswith("data:") else f"data:image/jpeg;base64,{image_base64}"

        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": image_content}}],
            }
        ]
        yield from self.chat_stream(messages, model_type=model_type, lang=lang)

    def get_embeddings(
        self, texts: list[str], model_type: str = "embedding", lang: str = "ru"
    ) -> list[list[float]] | None:
        self.logger.info(f"get_embeddings called with {len(texts)} texts, model_type={model_type}")
        config = get_model_config(model_type)
        if not config:
            self.logger.error(f"get_embeddings: no config for {model_type}")
            return None

        # Use model_type (module name like 'embedding') as model identifier for llama-swap
        # This maps to the 'id' field in llama-swap config
        model = model_type
        if not model:
            return None

        timeout = config.get("timeout", 120)
        self.logger.info(f"get_embeddings: calling backend with model={model}")
        result = self.backend.get_embeddings(texts, model, config, timeout)
        self.logger.info(f"get_embeddings: result type={type(result)}, len={len(result) if result else None}")
        return result  # type: ignore[no-any-return]

    def call(
        self,
        messages: list[dict],
        model_type: str = "chat",
        stream: bool = False,
        lang: str = "ru",
        validate: bool = True,
    ) -> str | dict[str, Any] | Generator[str, None, None]:
        if stream:
            return self.chat_stream(messages, model_type=model_type, lang=lang, validate=validate)
        return self.chat(messages, model_type=model_type, lang=lang, validate=validate)

    def unload_all_models(self) -> bool:
        return self.backend.unload_all_models()  # type: ignore[no-any-return]

    def get_running_models(self) -> list[str]:
        return self.backend.get_running_models()  # type: ignore[no-any-return]
