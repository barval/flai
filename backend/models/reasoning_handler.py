"""
Обработка сложных запросов через reasoning-модель
"""
from typing import List, Dict
from loguru import logger
from backend.utils.ollama_client import ollama
from backend.utils.config import settings
from backend.models.prompts import REASONING_PROMPT, get_current_time_str
from backend.utils.text_utils import clean_thinking_blocks

async def handle_reasoning(messages: List[Dict], session_id: str) -> Dict:
    current_time = get_current_time_str(settings.timezone)
    
    user_message = next(
        (m for m in reversed(messages) if m["role"] == "user"),
        {"content": ""}
    )
    user_query = user_message.get("content", "")
    
    prompt = REASONING_PROMPT.format(
        current_time_str=current_time,
        reasoning_query=user_query
    )
    
    chat_history = []
    for msg in messages[-15:]:
        if msg["role"] in ["user", "assistant"]:
            chat_history.append({
                "role": msg["role"],
                "content": msg["content"]
            })
    
    full_messages = [{"role": "user", "content": prompt}] + chat_history
    
    try:
        response = await ollama.chat(
            model=settings.llm_reasoning_model,
            messages=full_messages,
            temperature=settings.llm_reasoning_temperature,
            top_p=settings.llm_reasoning_top_p
        )
        content = response.get("message", {}).get("content", "").strip()
        content = clean_thinking_blocks(content)
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