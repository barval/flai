"""
Главный маршрутизатор запросов
"""
from typing import Dict, List, Optional
from loguru import logger

from .prompts import ROOM_NOT_FOUND
from .router_handler import classify_request, RouterResult
from .chat_handler import handle_chat
from .multimodal_handler import analyze_image
from .reasoning_handler import handle_reasoning
from .camera_handler import get_camera_feed
from .image_generator import generate_image


class RequestRouter:
    """Маршрутизация запросов к appropriate обработчикам"""
    
    async def route(
        self,
        session_id: str,
        messages: List[Dict],
        image_data: Optional[Dict] = None
    ) -> Dict:
        """
        Основная точка входа
        
        Логика:
        1. Если есть изображение → анализ через мультимодальную модель
        2. Если только текст → классификация через router-модель
        3. Маршрутизация к нужному обработчику
        """
        import time
        start_time = time.time()
        
        # === СЛУЧАЙ 1: Есть изображение ===
        if image_data:
            logger.info("Запрос с изображением → мультимодальный анализ")
            
            caption = None
            # Если у изображения есть текстовая подпись (последнее user-сообщение)
            user_msg = next((m for m in reversed(messages) if m["role"] == "user"), None)
            if user_msg and user_msg.get("content"):
                caption = user_msg["content"]
            
            result = await analyze_image(
                image_base64=image_data["base64"],
                caption=caption,
                session_id=session_id
            )
            
            duration = time.time() - start_time
            return {
                "success": True,
                "content": result["content"],
                "images": [],  # Анализ, не генерация
                "model_used": settings.llm_multimodal_model,
                "request_type": "multimodal_analysis",
                "duration_sec": round(duration, 2),
                "tokens": result.get("tokens", 0)
            }
        
        # === СЛУЧАЙ 2: Только текст ===
        user_message = next(
            (m for m in reversed(messages) if m["role"] == "user"),
            {"content": ""}
        )
        user_query = user_message.get("content", "")
        
        # Классификация через LLM-маршрутизатор
        classification = await classify_request(user_query)
        logger.info(f"Классификация: {classification.route_type}, payload: {classification.payload}")
        
        try:
            # Маршрутизация по типу
            if classification.route_type == RouterResult.SIMPLE:
                # Простой ответ уже в payload
                duration = time.time() - start_time
                return {
                    "success": True,
                    "content": classification.payload or "✓",
                    "images": [],
                    "model_used": settings.llm_chat_model,
                    "request_type": "simple",
                    "duration_sec": round(duration, 2),
                    "tokens": 0  # Ответ без вызова модели
                }
            
            elif classification.route_type == RouterResult.IMAGE_GEN:
                result = await generate_image(
                    user_query=classification.payload,
                    session_id=session_id
                )
                duration = time.time() - start_time
                result["model_used"] = f"A1111/{settings.a1111_model}"
                result["request_type"] = "image_generation"
                result["duration_sec"] = round(duration, 2)
                return result
            
            elif classification.route_type == RouterResult.CAMERA:
                if classification.payload == ROOM_NOT_FOUND:
                    content = ROOM_NOT_FOUND
                else:
                    cam_result = await get_camera_feed(classification.payload)
                    content = cam_result.get("message", cam_result.get("error", ""))
                
                duration = time.time() - start_time
                return {
                    "success": True,
                    "content": content,
                    "images": [],  # В демо: без изображения
                    "model_used": "camera_system",
                    "request_type": "camera_request",
                    "duration_sec": round(duration, 2),
                    "tokens": 0
                }
            
            elif classification.route_type == RouterResult.REASONING:
                result = await handle_reasoning(
                    messages=messages,
                    session_id=session_id
                )
                duration = time.time() - start_time
                return {
                    "success": True,
                    "content": result["content"],
                    "images": [],
                    "model_used": settings.llm_reasoning_model,
                    "request_type": "reasoning",
                    "duration_sec": round(duration, 2),
                    "tokens": result.get("tokens", 0)
                }
            
            else:  # UNKNOWN → fallback к reasoning
                result = await handle_reasoning(messages=messages, session_id=session_id)
                duration = time.time() - start_time
                return {
                    "success": True,
                    "content": result["content"],
                    "images": [],
                    "model_used": settings.llm_reasoning_model,
                    "request_type": "reasoning_fallback",
                    "duration_sec": round(duration, 2),
                    "tokens": result.get("tokens", 0)
                }
                
        except Exception as e:
            logger.error(f"Ошибка маршрутизации: {e}")
            return {
                "success": False,
                "error": str(e),
                "content": "❌ Внутренняя ошибка обработки запроса.",
                "model_used": None,
                "request_type": classification.route_type if 'classification' in locals() else "unknown"
            }
    
    async def get_model_status(self) -> Dict:
        """Статус всех моделей и сервисов"""
        from utils.ollama_client import ollama
        from utils.a1111_client import a1111
        
        return {
            "ollama": {
                "available": await ollama.is_model_available(settings.llm_chat_model),
                "models": [
                    settings.llm_chat_model,
                    settings.llm_multimodal_model, 
                    settings.llm_reasoning_model
                ]
            },
            "automatic1111": {
                "available": await a1111.is_available(),
                "model": settings.a1111_model
            },
            "router_model": settings.llm_chat_model
        }


# Глобальный экземпляр
router = RequestRouter()