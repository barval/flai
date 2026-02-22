"""
Обработка сложных запросов через reasoning-модель

Этот модуль отвечает за обработку запросов, которые требуют:
- Математических вычислений
- Логических рассуждений
- Написания кода
- Создания длинных текстов
- Многошагового анализа
"""
from typing import List, Dict
from loguru import logger

from backend.utils.ollama_client import ollama
from backend.utils.config import settings
from backend.models.prompts import REASONING_PROMPT, get_current_time_str


async def handle_reasoning(
    messages: List[Dict],
    session_id: str
) -> Dict:
    """
    Обработка сложного запроса через reasoning-модель
    
    Args:
        messages: история сообщений в формате OpenAI
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
    
    # Формируем системный промпт для reasoning-модели
    prompt = REASONING_PROMPT.format(
        current_time_str=current_time,
        reasoning_query=user_query
    )
    
    # Контекст: последние сообщения для связности ответа
    # Для сложных задач берём больше контекста
    chat_history = []
    for msg in messages[-15:]:  # Последние 15 сообщений
        if msg["role"] in ["user", "assistant"]:
            chat_history.append({
                "role": msg["role"],
                "content": msg["content"]
            })
    
    # Добавляем системный промпт как первое сообщение
    full_messages = [{"role": "user", "content": prompt}] + chat_history
    
    try:
        # Запрос к reasoning-модели
        response = await ollama.chat(
            model=settings.llm_reasoning_model,
            messages=full_messages,
            temperature=settings.llm_reasoning_temperature,
            top_p=settings.llm_reasoning_top_p
        )
        
        content = response.get("message", {}).get("content", "").strip()
        tokens = response.get("eval_count", 0) + response.get("prompt_eval_count", 0)
        
        logger.info(
            f"Reasoning response ({settings.llm_reasoning_model}): "
            f"{content[:150]}{'...' if len(content) > 150 else ''}"
        )
        
        return {
            "content": content,
            "tokens": tokens
        }
        
    except Exception as e:
        logger.error(f"Ошибка reasoning-модели: {e}", exc_info=True)
        return {
            "content": "❌ Ошибка обработки сложного запроса. Попробуйте переформулировать или разбить на части.",
            "tokens": 0
        }