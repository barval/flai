"""
Обработка простых текстовых запросов через чат-модель

Этот модуль отвечает за обработку запросов, которые были классифицированы
как "простые" — то есть ответ на них уже содержится во входных данных
(текущее время, день недели и т.п.) или не требует сложных рассуждений.
"""
from typing import List, Dict
from loguru import logger

from backend.utils.ollama_client import ollama
from backend.utils.config import settings
from backend.models.prompts import CHAT_PROMPT, get_current_time_str


async def handle_chat(
    messages: List[Dict],
    session_id: str
) -> Dict:
    """
    Обработка простого запроса через чат-модель
    
    Args:
        messages: история сообщений в формате OpenAI:
            [
                {"role": "user", "content": "..."},
                {"role": "assistant", "content": "..."},
                ...
            ]
        session_id: идентификатор сессии для логирования
    
    Returns:
        dict с ответом и метаданными:
        {
            "content": str,  # текст ответа
            "tokens": int    # количество использованных токенов
        }
    """
    current_time = get_current_time_str(settings.timezone)
    
    # Получаем последний пользовательский запрос
    user_message = next(
        (m for m in reversed(messages) if m["role"] == "user"),
        {"content": ""}
    )
    user_query = user_message.get("content", "")
    
    # Формируем системный промпт
    prompt = CHAT_PROMPT.format(
        current_time_str=current_time,
        user_query=user_query
    )
    
    # Подготовка контекста для модели (только последние N сообщений)
    # Это экономит контекстное окно и ускоряет ответ
    chat_history = []
    for msg in messages[-10:]:  # Последние 10 сообщений
        if msg["role"] in ["user", "assistant"]:
            chat_history.append({
                "role": msg["role"],
                "content": msg["content"]
            })
    
    # Добавляем системный промпт как первое сообщение
    full_messages = [{"role": "user", "content": prompt}] + chat_history
    
    try:
        # Запрос к чат-модели
        response = await ollama.chat(
            model=settings.llm_chat_model,
            messages=full_messages,
            temperature=settings.llm_chat_temperature,
            top_p=settings.llm_chat_top_p
        )
        
        # Извлечение ответа
        content = response.get("message", {}).get("content", "").strip()
        
        # Подсчёт токенов (если модель вернула метрики)
        tokens = response.get("eval_count", 0) + response.get("prompt_eval_count", 0)
        
        logger.info(
            f"Chat response ({settings.llm_chat_model}): "
            f"{content[:100]}{'...' if len(content) > 100 else ''}"
        )
        
        return {
            "content": content,
            "tokens": tokens
        }
        
    except Exception as e:
        logger.error(f"Ошибка чат-модели: {e}", exc_info=True)
        return {
            "content": "❌ Ошибка обработки запроса. Попробуйте ещё раз.",
            "tokens": 0
        }