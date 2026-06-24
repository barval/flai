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
import re
import time
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
        return result  # type: ignore[no-any-return]
    except Exception:
        return key.format(**kwargs) if kwargs else key


def _extract_error_message(response: Any) -> str:
    """Extract a human-readable error message from a llama.cpp HTTP response.

    Tries to parse JSON {"error": {"message": "..."}} and falls back to raw body.
    Used to surface the real reason (e.g., "Failed to load image or audio file")
    instead of a generic "HTTP error 400" to the user.
    """
    try:
        body = response.text[:500] if hasattr(response, "text") else str(response)[:500]
    except Exception:
        body = ""
    if not body:
        return ""
    try:
        data = response.json() if hasattr(response, "json") else None
    except Exception:
        data = None
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            msg = err.get("message")
            if msg:
                return str(msg)
        elif isinstance(err, str) and err:
            return err
    return body


def _translate_llama_swap_error(msg: str, lang: str = "ru") -> str:
    """Translate known llama-swap error messages to user-friendly text.

    llama-swap returns raw English errors (e.g., "could not find suitable inference handler
    for X.gguf"). These are not in .po files, so we map them manually.
    """
    lower = msg.lower()
    if "could not find suitable inference handler for" in lower:
        # Extract model name: "... for <model_name>" or "... for <model_name>.gguf"
        parts = msg.rsplit(" for ", 1)
        model_id = parts[-1].strip().rstrip(".").strip() if len(parts) > 1 else ""
        # Strip .gguf for display
        model_display = model_id.removesuffix(".gguf")
        return _tr("Model '{model}' is not available. Select a different model in admin panel.", lang, model=model_display)
    return msg


def _format_user_error(response: Any, lang: str = "ru") -> str:
    """Build a user-facing error string with the "⚠️ " prefix.

    Uses _extract_error_message to surface llama.cpp's real reason; falls back
    to a translated generic "HTTP error {status}" string if extraction fails.
    """
    msg = _extract_error_message(response)
    if msg:
        translated = _translate_llama_swap_error(msg, lang)
        return translated if translated.startswith("⚠️") else f"⚠️ {translated}"
    status = getattr(response, "status_code", 0) or 0
    return f"⚠️ {_tr('HTTP error {status}', lang, status=status)}"


# ── Thinking tag filters ──────────────────────────────────────────────
# Models like Qwen, DeepSeek, Gemma use: <think>...</think>
# gpt-oss-20b uses:  <|channel|>analysis<|message|>...<|end|>  (reasoning)
#                    <|channel|>commentary<|message|>...<|end|>  (actual answer)
# Only the `analysis` channel is thinking — `commentary` IS the answer.
# These filters strip reasoning blocks from both streaming and non-streaming output.

_THINK_OPEN_RE = re.compile(r"<think[\s>]|<\|channel\|>analysis<\|message\|>")
_THINK_CLOSE_RE = re.compile(r"</think>|<\|end\|>")


def _strip_thinking_tags(text: str) -> str:
    """Remove thinking/reasoning blocks from model output and unwrap answer channels.

    Handles:
    - <think>...</think> blocks (Qwen, DeepSeek, Gemma, QwQ)
    - <|channel|>analysis<|message|>...<|end|> — reasoning, stripped entirely
    - <|channel|>commentary<|message|>...ANSWER...<|end|> — unwrapped (answer kept)
    - Malformed <|channel|>... (no <|message|>) — stripped (broken reasoning leftovers)
    """
    if not text or ("<think" not in text and "<|channel|>" not in text):
        return text
    text = re.sub(r"<think[\s>][\s\S]*?</think>", "", text)
    # Strip <|channel|>analysis<|message|>...<|end|> reasoning blocks
    text = re.sub(r"<\|channel\|>analysis<\|message\|>[\s\S]*?<\|end\|>", "", text)
    text = re.sub(r"<\|channel\|>analysis<\|message\|>[\s\S]*$", "", text)
    # Unwrap <|channel|>commentary<|message|>...<|end|> — keep inner content
    text = re.sub(r"<\|channel\|>commentary<\|message\|>([\s\S]*?)<\|end\|>", r"\1", text)
    # Strip malformed <|channel|>... without <|message|> (e.g. <|channel|>commentary to=...)
    text = re.sub(r"<\|channel\|>[^<]*$", "", text)
    return text.strip()


# ── Generic reasoning pattern filter ────────────────────────────────────
# Some reasoning models (gpt-oss-20b, QwQ, gemma-4) output chain-of-thought
# as plain text without thinking tags.  These patterns detect common
# reasoning markers and strip everything up to the actual answer.

_REASONING_MARKERS_RE = re.compile(
    r"(?:"
    r"The user (?:asked|is asking|asks|said|wants|wondered)"
    r"|Пользователь (?:спросил|спрашивает|просит|хочет|говорит|спрашивал)"
    r"|(?:Analyze|Analyse|Check|Formulate|Identify|Review|Consider|Plan|Commentary) \w+[\s:]+"
    r"|(?:Self-Correction|Refinement)[\s:]+"
    r"|(?:I need to|I should|I must|Let me|Let's|"
    r"Мне нужно|Мне следует|Мне необходимо)"
    r"|(?:Final Answer(?: Generation)?(?:\s*\([^)]*\))?|Генерация финального ответа|Финальный ответ)[\s:]*"
    r"|(?:Это (?:вопрос|задача|запрос)|This is a (?:question|task|request))"
    r"|(?:Ответ (?:должен|должна|будет|стоит)|The answer (?:should|must|will))"
    r"|(?:Для (?:этого|данного) (?:вопроса|запроса)|For this (?:question|request))"
    r"|(?:Мой ответ|Моё задание|My (?:answer|task))"
    r"|(?:использовать инструмент|use tool)"
    r")",
    re.IGNORECASE,
)

# Matches markdown plan lines: "** State ...", "** Answer the ...", "** First ..."
# These are model-generated meta-instructions that should not be shown to the user.
_MD_PLAN_LINE_RE = re.compile(r"^\s*\*\*\s+\w", re.MULTILINE)


def _strip_generic_reasoning(text: str) -> str:
    """Strip chain-of-thought reasoning output as plain text (no thinking tags).

    Some reasoning models output step-by-step analysis before the actual answer
    without wrapping it in `` tags.  This function detects common reasoning
    markers (e.g. "Analyze Persona:", "Final Answer Generation:") and returns
    only the text after the last marker.

    Also strips markdown plan lines ("** State the identity...") that some
    models (gemma-4) generate instead of a real answer.

    If no markers are found AND no markdown plan lines exist,
    the text is returned unchanged (avoids false positives).

    If the text is identified as reasoning but stripping would leave an empty
    or very short answer, the original text is returned instead of an empty
    string — better to show raw reasoning than to error out on the user.
    """
    if not text or len(text) < 30:
        return text

    # Check for known reasoning markers
    matches = list(_REASONING_MARKERS_RE.finditer(text))
    if len(matches) >= 2:
        last_match = matches[-1]
        answer = text[last_match.end():].strip()

        # High marker density in first 200 chars = pure reasoning, no real answer
        # If stripping would leave empty, return original text instead of erroring out
        first_200 = text[:200]
        density = len(list(_REASONING_MARKERS_RE.finditer(first_200)))
        if density >= 3 and len(answer) < 100:
            return text

        # After last marker — if remaining text is short, it's still reasoning
        if len(answer) < 30:
            return text
        return answer

    # Check for markdown plan lines — if ALL non-empty lines start with "** ",
    # the model produced only a plan and no real answer.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and all(_MD_PLAN_LINE_RE.match(ln) for ln in lines):
        return text

    return text


def _process_stream_chunk(buffer: str, thinking_active: bool) -> tuple[str, str, bool]:
    """Process a streaming chunk, stripping thinking tags.

    Stateful filter that tracks whether we're inside a thinking block.
    Handles partial tags split across multiple streaming chunks.

    Returns (output_text, remaining_buffer, new_thinking_state).
    """
    output = ""

    while buffer:
        if thinking_active:
            close_match = _THINK_CLOSE_RE.search(buffer)
            if close_match:
                buffer = buffer[close_match.end():]
                thinking_active = False
            else:
                break
        else:
            open_match = _THINK_OPEN_RE.search(buffer)
            if open_match:
                output += buffer[:open_match.start()]
                buffer = buffer[open_match.start():]
                thinking_active = True
            else:
                safe_len = max(len(buffer) - 30, 0)
                if safe_len > 0:
                    output += buffer[:safe_len]
                    buffer = buffer[safe_len:]
                break

    return output, buffer, thinking_active


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
        self,
        messages: list[dict],
        model: str,
        config: dict,
        timeout: int,
        lang: str,
        model_type: str = "chat",
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> str | dict[str, Any]:
        raise NotImplementedError

    def chat_stream(
        self,
        messages: list[dict],
        model: str,
        config: dict,
        timeout: int,
        lang: str,
        model_type: str = "chat",
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> Generator[str | dict[str, Any], None, None]:
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
        self,
        messages: list[dict],
        model: str,
        config: dict,
        timeout: int,
        lang: str,
        model_type: str = "chat",
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> str | dict[str, Any]:
        base_url = self.get_base_url()
        context = config.get("context_length", 4096)
        temp = temperature if temperature is not None else config.get("temperature", 0.7)
        top_p = config.get("top_p", 0.9)
        repeat_penalty = config.get("repeat_penalty", 1.1)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "max_tokens": context,
            "temperature": temp,
            "top_p": top_p,
            "repeat_penalty": repeat_penalty,
            "stop": ["</s>", "<|eot_id|>"],
        }
        if tools:
            payload["tools"] = tools

        if not self.circuit_breaker.can_execute():
            return _tr("Service temporarily unavailable. Circuit breaker is open after repeated failures.", lang)

        try:
            response = requests.post(f"{base_url}/v1/chat/completions", json=payload, timeout=timeout)
            if response.status_code == 200:
                result = response.json()
                choices = result.get("choices", [])
                if not choices:
                    return _tr("Model returned empty response", lang)

                message = choices[0].get("message", {})
                content = message.get("content", "")
                tool_calls = message.get("tool_calls")

                if content is None:
                    content = ""

                if tool_calls:
                    self.circuit_breaker.record_success()
                    return {"content": content, "tool_calls": tool_calls}

                for stop_token in ["</s>", "<|eot_id|>"]:
                    if stop_token in content:
                        content = content[: content.index(stop_token)]

                self.circuit_breaker.record_success()
                result = _strip_thinking_tags(content.strip())
                result = _strip_generic_reasoning(result)
                return result  # type: ignore[no-any-return]
            else:
                self.circuit_breaker.record_failure()
                self.logger.error(
                    f"chat HTTP {response.status_code} from {model_type}: {response.text[:500] if hasattr(response, 'text') else ''}"
                )
                return _format_user_error(response, lang)
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
        self,
        messages: list[dict],
        model: str,
        config: dict,
        timeout: int,
        lang: str,
        model_type: str = "chat",
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> Generator[str | dict[str, Any], None, None]:
        base_url = self.get_base_url()
        context = config.get("context_length", 4096)
        temp = temperature if temperature is not None else config.get("temperature", 0.7)
        top_p = config.get("top_p", 0.9)
        repeat_penalty = config.get("repeat_penalty", 1.1)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "max_tokens": context,
            "temperature": temp,
            "top_p": top_p,
            "repeat_penalty": repeat_penalty,
            "stop": ["</s>", "<|eot_id|>"],
        }
        if tools:
            payload["tools"] = tools

        if not self.circuit_breaker.can_execute():
            yield _tr("Service temporarily unavailable. Circuit breaker is open after repeated failures.", lang)
            return

        response = None
        try:
            response = requests.post(f"{base_url}/v1/chat/completions", json=payload, timeout=timeout, stream=True)
            if response.status_code != 200:
                self.circuit_breaker.record_failure()
                self.logger.error(
                    f"chat_stream HTTP {response.status_code} from {model_type}: {response.text[:500] if hasattr(response, 'text') else ''}"
                )
                yield _format_user_error(response, lang)
                return

            self.circuit_breaker.record_success()
            # Stateful thinking tag filter — strips reasoning blocks
            # from any model type (Qwen, DeepSeek, gpt-oss, etc.)
            _thinking_active = False
            _stream_buffer = ""
            # Accumulate tool calls from streaming chunks
            _tool_calls_by_index: dict[int, dict[str, Any]] = {}

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
                        _stream_buffer += content
                        output, _stream_buffer, _thinking_active = _process_stream_chunk(
                            _stream_buffer, _thinking_active,
                        )
                        if output:
                            yield output
                    # Accumulate tool call deltas
                    tc_deltas = delta.get("tool_calls")
                    if tc_deltas:
                        for tc_delta in tc_deltas:
                            idx = tc_delta.get("index", 0)
                            if idx not in _tool_calls_by_index:
                                func = tc_delta.get("function", {})
                                _tool_calls_by_index[idx] = {
                                    "id": tc_delta.get("id", ""),
                                    "type": "function",
                                    "function": {
                                        "name": func.get("name", ""),
                                        "arguments": func.get("arguments", ""),
                                    },
                                }
                            else:
                                tc = _tool_calls_by_index[idx]
                                func_delta = tc_delta.get("function", {})
                                if func_delta.get("name"):
                                    tc["function"]["name"] += func_delta["name"]
                                if func_delta.get("arguments"):
                                    tc["function"]["arguments"] += func_delta["arguments"]
                                if tc_delta.get("id"):
                                    tc["id"] = tc_delta["id"]
                except json.JSONDecodeError:
                    continue
            # Flush remaining buffer (non-thinking tail)
            if _stream_buffer and not _thinking_active:
                yield _stream_buffer
            # If tool calls were accumulated, yield them as a dict
            if _tool_calls_by_index:
                tool_calls = [_tool_calls_by_index[i] for i in sorted(_tool_calls_by_index.keys())]
                yield {"type": "tool_calls", "tool_calls": tool_calls}
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

    def __init__(self, app=None) -> None:
        super().__init__(app)
        # Separate circuit breakers per model type — prevents one model's failures
        # (e.g., reasoning OOM) from blocking another model (e.g., chat).
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._degraded_models: set[str] = set()

    def _get_circuit_breaker(self, model_type: str) -> CircuitBreaker:
        """Get or create a circuit breaker for the given model type."""
        if model_type not in self._circuit_breakers:
            self._circuit_breakers[model_type] = CircuitBreaker(
                failure_threshold=3, recovery_timeout=60
            )
        return self._circuit_breakers[model_type]

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
        """Record circuit breaker failure and degrade on ANY failure.

        Degradation reduces n_gpu_layers so the model fits in available VRAM.
        """
        cb = self._get_circuit_breaker(model_type)
        cb.record_failure()
        # Degrade on every failure — not just circuit breaker open —
        # to adapt VRAM usage immediately after the first crash.
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

    def call_cpu(
        self,
        messages: list[dict],
        model: str = "merge_cpu",
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> str:
        """CPU-only LLM call — bypasses VRAM management entirely.

        Used for background tasks (fact_merge) that don't need GPU.
        The merge_cpu model runs on CPU with a reduced context window.
        """
        base_url = self.get_base_url()
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        try:
            import httpx

            response = httpx.post(
                f"{base_url}/v1/chat/completions",
                json=payload,
                timeout=300,
            )
            response.raise_for_status()
            data = response.json()
            return str(data["choices"][0]["message"]["content"])
        except Exception as e:
            self.logger.error(f"CPU LLM call failed: {e}")
            return ""

    def chat(
        self,
        messages: list[dict],
        model: str,
        config: dict,
        timeout: int,
        lang: str,
        model_type: str = "chat",
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> str | dict[str, Any]:
        base_url = self.get_base_url()
        temp = temperature if temperature is not None else config.get("temperature", 0.7)
        top_p = config.get("top_p", 0.9)
        repeat_penalty = config.get("repeat_penalty", 1.1)

        model_name = model

        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "stream": False,
            "temperature": temp,
            "top_p": top_p,
            "repeat_penalty": repeat_penalty,
            "stop": ["</s>", "<|eot_id|>"],
        }
        if tools:
            payload["tools"] = tools

        self.logger.info(f"LlamaSwapBackend request: model={model}, payload keys={list(payload.keys())}")

        max_retries = 1 if model_type in ("multimodal", "reasoning", "chat") else 0

        for attempt in range(max_retries + 1):
            cb = self._get_circuit_breaker(model_type)
            if not cb.can_execute():
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

                    message = choices[0].get("message", {})
                    content = message.get("content", "")
                    tool_calls = message.get("tool_calls")

                    if content is None:
                        content = ""

                    if tool_calls:
                        cb.record_success()
                        return {"content": content, "tool_calls": tool_calls}  # type: ignore[return-value]

                    for stop_token in ["</s>", "<|eot_id|>"]:
                        if stop_token in content:
                            content = content[: content.index(stop_token)]

                    cb.record_success()

                    # Measure VRAM after successful model load
                    try:
                        ngl = config.get("n_gpu_layers", -1)
                        ctx_size = config.get("context_length", 4096)
                        from app.resource_manager import get_resource_manager
                        rm = get_resource_manager()
                        rm.measure_model_vram(model_type, model_name, ctx_size, ngl)
                    except Exception:
                        pass

                    result = _strip_thinking_tags(content.strip())
                    result = _strip_generic_reasoning(result)
                    return result  # type: ignore[no-any-return]
                else:
                    if attempt < max_retries and response.status_code in (500, 502):
                        self.logger.warning(f"chat {response.status_code} on attempt {attempt + 1}, retrying in 5s")
                        time.sleep(5)
                        continue
                    self._record_llama_failure(model_type)
                    err_body = response.text[:500]
                    self.logger.error(f"chat HTTP {response.status_code} from {model_type}: {err_body}")
                    return _format_user_error(response, lang)
            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    self.logger.warning(f"chat timeout on attempt {attempt + 1}, retrying in 2s")
                    time.sleep(2)
                    continue
                self._record_llama_failure(model_type)
                return _tr(
                    "Timeout ({timeout}s) when calling the model. Try increasing timeout in admin panel or simplify your request.",
                    lang,
                    timeout=timeout,
                )
            except requests.exceptions.ConnectionError:
                if attempt < max_retries:
                    self.logger.warning(f"chat connection error on attempt {attempt + 1}, retrying in 2s")
                    time.sleep(2)
                    continue
                self._record_llama_failure(model_type)
                return _tr("Could not connect to llama-swap", lang)
            except Exception as e:
                if attempt < max_retries:
                    self.logger.warning(f"chat error on attempt {attempt + 1}, retrying in 2s: {e}")
                    time.sleep(2)
                    continue
                self.logger.error(f"Error: {e}")
                return f"{_tr('Error', lang)}: {str(e)}"

        # Defensive: if the loop falls through without hitting any of the
        # explicit returns above (e.g. unexpected control flow), return a
        # user-facing error rather than an implicit None.
        return _tr("Internal error: no response from model", lang)

    def chat_stream(
        self,
        messages: list[dict],
        model: str,
        config: dict,
        timeout: int,
        lang: str,
        model_type: str = "chat",
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> Generator[str | dict[str, Any], None, None]:
        base_url = self.get_base_url()
        temp = temperature if temperature is not None else config.get("temperature", 0.7)
        top_p = config.get("top_p", 0.9)
        repeat_penalty = config.get("repeat_penalty", 1.1)
        model_name = model

        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "stream": True,
            "temperature": temp,
            "top_p": top_p,
            "repeat_penalty": repeat_penalty,
            "stop": ["</s>", "<|eot_id|>"],
        }
        if tools:
            payload["tools"] = tools

        max_retries = 1 if model_type in ("multimodal", "reasoning", "chat") else 0
        response = None

        try:
            for attempt in range(max_retries + 1):
                cb = self._get_circuit_breaker(model_type)
                if not cb.can_execute():
                    self._degrade_model_if_needed(model_type)
                    yield _tr("Service temporarily unavailable. Circuit breaker is open after repeated failures.", lang)
                    return

                try:
                    response = requests.post(
                        f"{base_url}/v1/chat/completions", json=payload, timeout=timeout, stream=True
                    )
                    if response.status_code != 200:
                        # Retry on 502 (typical transient failure) OR on
                        # "Failed to load image" 400 (race condition when
                        # multimodal model was just reloaded with degraded
                        # n_gpu_layers and is still loading the image stack).
                        is_image_load_400 = False
                        if response.status_code == 400 and model_type == "multimodal":
                            try:
                                err_msg = _extract_error_message(response).lower()
                                is_image_load_400 = "failed to load image" in err_msg
                            except Exception:
                                is_image_load_400 = False
                        if attempt < max_retries and (
                            response.status_code == 502
                            or is_image_load_400
                        ):
                            delay = 5 if response.status_code == 502 else 1
                            reason = "502" if response.status_code == 502 else "image-load-400"
                            self.logger.warning(
                                f"chat_stream {reason} on attempt {attempt + 1}, retrying in {delay}s"
                            )
                            time.sleep(delay)
                            continue
                        self._record_llama_failure(model_type)
                        err_body = response.text[:500]
                        self.logger.error(f"chat_stream HTTP {response.status_code} from {model_type}: {err_body}")
                        yield _format_user_error(response, lang)
                        return

                    cb.record_success()

                    # Measure VRAM after successful model load
                    try:
                        ngl = config.get("n_gpu_layers", -1)
                        ctx_size = config.get("context_length", 4096)
                        from app.resource_manager import get_resource_manager
                        rm = get_resource_manager()
                        rm.measure_model_vram(model_type, model_name, ctx_size, ngl)
                    except Exception:
                        pass

                    # Stateful thinking tag filter — strips reasoning blocks
                    # from any model type (Qwen, DeepSeek, gpt-oss, etc.)
                    _thinking_active = False
                    _stream_buffer = ""
                    # Accumulate tool calls from streaming chunks
                    _tool_calls_by_index: dict[int, dict[str, Any]] = {}

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
                                _stream_buffer += content
                                output, _stream_buffer, _thinking_active = _process_stream_chunk(
                                    _stream_buffer, _thinking_active,
                                )
                                if output:
                                    yield output
                            # Accumulate tool call deltas
                            tc_deltas = delta.get("tool_calls")
                            if tc_deltas:
                                for tc_delta in tc_deltas:
                                    idx = tc_delta.get("index", 0)
                                    if idx not in _tool_calls_by_index:
                                        func = tc_delta.get("function", {})
                                        _tool_calls_by_index[idx] = {
                                            "id": tc_delta.get("id", ""),
                                            "type": "function",
                                            "function": {
                                                "name": func.get("name", ""),
                                                "arguments": func.get("arguments", ""),
                                            },
                                        }
                                    else:
                                        tc = _tool_calls_by_index[idx]
                                        func_delta = tc_delta.get("function", {})
                                        if func_delta.get("name"):
                                            tc["function"]["name"] += func_delta["name"]
                                        if func_delta.get("arguments"):
                                            tc["function"]["arguments"] += func_delta["arguments"]
                                        if tc_delta.get("id"):
                                            tc["id"] = tc_delta["id"]
                        except json.JSONDecodeError:
                            continue
                    # Flush remaining buffer
                    if _stream_buffer:
                        if _thinking_active:
                            # Stream ended inside <|channel|>analysis block (no <|end|>).
                            # Strip opening tag, yield whatever remains.
                            _stream_buffer = re.sub(
                                r"^.*?<\|channel\|>analysis<\|message\|>", "", _stream_buffer
                            )
                        if _stream_buffer:
                            yield _stream_buffer
                    # If tool calls were accumulated, yield them as a dict
                    if _tool_calls_by_index:
                        tool_calls = [_tool_calls_by_index[i] for i in sorted(_tool_calls_by_index.keys())]
                        yield {"type": "tool_calls", "tool_calls": tool_calls}
                    break  # success, exit retry loop
                except requests.exceptions.Timeout:
                    if attempt < max_retries:
                        self.logger.warning(f"chat_stream timeout on attempt {attempt + 1}, retrying in 2s")
                        time.sleep(2)
                        continue
                    self._record_llama_failure(model_type)
                    yield _tr(
                        "Timeout ({timeout}s) when calling the model. Try increasing timeout in admin panel or simplify your request.",
                        lang,
                        timeout=timeout,
                    )
                except requests.exceptions.ConnectionError:
                    if attempt < max_retries:
                        self.logger.warning(f"chat_stream connection error on attempt {attempt + 1}, retrying in 2s")
                        time.sleep(2)
                        continue
                    self._record_llama_failure(model_type)
                    yield _tr("Could not connect to llama-swap", lang)
                    return
                except Exception as e:
                    if attempt < max_retries:
                        self.logger.warning(f"chat_stream error on attempt {attempt + 1}, retrying in 2s: {e}")
                        time.sleep(2)
                        continue
                    self.logger.error(f"Stream error: {e}")
                    yield f"{_tr('Error', lang)}: {str(e)}"
                    return
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
                return response.json().get("running", [])  # type: ignore[no-any-return]
            return []
        except Exception:
            return []


class LlamaCppClient:
    """Client for llama-server/llama-swap via backend pattern."""

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.available = False
        self.app = app
        self._active_model_type = None

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
        # If chat model is preloaded, mark it as active to skip full ensure_vram on first request
        if self.available and self._active_model_type is None:
            try:
                swap_url = os.getenv("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")
                resp = requests.get(f"{swap_url.rstrip('/')}/running", timeout=2)
                if resp.status_code == 200:
                    models = resp.json().get("running", [])
                    from app.model_config import get_model_config

                    config = get_model_config("chat")
                    model_name = config.get("model_name", "") if config else ""
                    if model_name and any(model_name in m.get("cmd", "") for m in models):
                        self._active_model_type = "chat"
                        self.logger.info("check_availability: chat model preloaded, marked as active")
            except Exception:
                pass
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
                        # Vision models tokenize images into many more tokens than
                        # a naive estimate. Qwen3VL uses dynamic tiling which can
                        # produce thousands of vision tokens per image.
                        total_tokens += 4096

        if total_tokens > hard_limit:
            return self._translate("Request too long, please simplify your request", lang)
        return None

    def reset_active_model(self):
        """Invalidate cached active model type when model is unloaded externally."""
        self._active_model_type = None

    def _ensure_vram(self, model_type: str) -> bool:
        """Ensure enough VRAM before a model call.

        Hybrid strategy:
        - For chat: skip if already active (router→response same instance, ~0ms)
        - For other types: stateless /running check (safe across workers, ~300ms)
        - Fallback: full unload + reload via ResourceManager
        """
        # === Chat skip: router and response go through same base.py.llamacpp instance ===
        if model_type == "chat" and self._active_model_type == "chat":
            # Quick sanity check: verify the model is actually still loaded
            try:
                swap_url = os.getenv("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")
                resp = requests.get(f"{swap_url.rstrip('/')}/running", timeout=2)
                if resp.status_code == 200:
                    models = resp.json().get("running", [])
                    config = get_model_config("chat")
                    model_name = config.get("model_name", "") if config else ""
                    if model_name and any(model_name in m.get("cmd", "") for m in models):
                        self.logger.debug("VRAM skip: chat model already active")
                        return True
            except Exception:
                pass
            # Model was unloaded externally — clear flag and fall through to full ensure_vram
            self.logger.debug("VRAM skip failed: chat model not in /running — full reload needed")
            self._active_model_type = None

        # === Stateless check: verify model is loaded via llama-swap + nvidia-smi ===
        try:
            swap_url = os.getenv("LLAMA_SWAP_URL", "http://flai-llamaswap:8080")
            resp = requests.get(f"{swap_url.rstrip('/')}/running", timeout=3)
            if resp.status_code == 200:
                models = resp.json().get("running", [])
                if len(models) == 1:
                    config = get_model_config(model_type)
                    model_name = config.get("model_name", "") if config else ""
                    if model_name and model_name in models[0].get("cmd", ""):
                        import subprocess

                        out = subprocess.run(
                            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if out.returncode == 0:
                            free = int(out.stdout.strip().split("\n")[0].strip())
                            from app.resource_manager import get_resource_manager

                            rm = get_resource_manager()
                            needed = rm.get_vram_needed_mb(model_type)
                            if free >= needed:
                                self._active_model_type = model_type
                                self.logger.debug(
                                    f"VRAM skip: {model_type} already loaded, "
                                    f"{free}MB free >= {needed}MB needed"
                                )
                                return True
        except Exception:
            pass

        # === Full ensure_vram: unload + poll ===
        from app.resource_manager import get_resource_manager

        rm = get_resource_manager()
        ok = rm.ensure_vram_for(model_type)
        if ok:
            self._active_model_type = model_type
        else:
            self._active_model_type = None
        return ok

    def chat(
        self, messages: list[dict], model_type: str = "chat", lang: str = "ru", validate: bool = True,
        tools: list[dict] | None = None, temperature: float | None = None,
    ) -> str | dict[str, Any]:
        if validate:
            error = self._validate_prompt(messages, model_type, lang)
            if error:
                return error

        if not self._ensure_vram(model_type):
            return _tr("GPU memory unavailable. Please try again.", lang)

        config = get_model_config(model_type)
        if not config:
            return self._translate("Model configuration missing", lang)

        model = config.get("model_name")
        if not model:
            return self._translate("Model for {model_type} not configured", lang, model_type=model_type)

        timeout = config.get("timeout", 300)
        return self.backend.chat(messages, model, config, timeout, lang, model_type=model_type, tools=tools, temperature=temperature)  # type: ignore[no-any-return]

    def chat_stream(
        self, messages: list[dict], model_type: str = "chat", lang: str = "ru", validate: bool = True,
        tools: list[dict] | None = None, temperature: float | None = None,
    ) -> Generator[str | dict[str, Any], None, None]:
        if validate:
            error = self._validate_prompt(messages, model_type, lang)
            if error:
                yield error
                return

        if not self._ensure_vram(model_type):
            yield _tr("GPU memory unavailable. Please try again.", lang)
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
        yield from self.backend.chat_stream(messages, model, config, timeout, lang, model_type=model_type, tools=tools, temperature=temperature)

    def chat_with_image(self, text: str, image_base64: str, model_type: str = "multimodal", lang: str = "ru") -> str:
        image_content = image_base64 if image_base64.startswith("data:") else f"data:image/jpeg;base64,{image_base64}"

        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": image_content}}],
            }
        ]
        return self.chat(messages, model_type=model_type, lang=lang)  # type: ignore[return-value]

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
        yield from self.chat_stream(messages, model_type=model_type, lang=lang)  # type: ignore[misc]

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
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> str | dict[str, Any] | Generator[str | dict[str, Any], None, None]:
        if stream:
            return self.chat_stream(messages, model_type=model_type, lang=lang, validate=validate, tools=tools, temperature=temperature)
        return self.chat(messages, model_type=model_type, lang=lang, validate=validate, tools=tools, temperature=temperature)

    def unload_all_models(self) -> bool:
        return self.backend.unload_all_models()  # type: ignore[no-any-return]

    def get_running_models(self) -> list[str]:
        return self.backend.get_running_models()  # type: ignore[no-any-return]
