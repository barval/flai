"""
Обработка простых текстовых запросов через чат-модель
"""
from typing import List, Dict
from loguru import logger
from backend.utils.ollama_client import ollama
from backend.utils.config import settings
from backend.models.prompts import CHAT_PROMPT, get_current_time_str

async def handle_chat(messages: List[Dict], session_id: str) -> Dict:
    current_time = get_current_time_str(settings.timezone)
    
    user_message = next(
        (m for m in reversed(messages) if m["role"] == "user"),
        {"content": ""}
    )
    user_query = user_message.get("content", "")
    
    prompt = CHAT_PROMPT.format(
        current_time_str=current_time,
        user_query=user_query
    )
    
    chat_history = []
    for msg in messages[-10:]:
        if msg["role"] in ["user", "assistant"]:
            chat_history.append({
                "role": msg["role"],
                "content": msg["content"]
            })
    
    full_messages = [{"role": "user", "content": prompt}] + chat_history
    
    try:
        response = await ollama.chat(
            model=settings.llm_chat_model,
            messages=full_messages,
            temperature=settings.llm_chat_temperature,
            top_p=settings.llm_chat_top_p
        )
        content = response.get("message", {}).get("content", "").strip()
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