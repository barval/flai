"""
Главный маршрутизатор запросов ИИ Локальный
"""
from typing import Dict, List, Optional
from loguru import logger
from backend.utils.config import settings
from backend.models.prompts import ROOM_NOT_FOUND
from backend.models.router_handler import classify_request, RouterResult
from backend.models.chat_handler import handle_chat
from backend.models.multimodal_handler import analyze_image, prepare_sd_prompt
from backend.models.reasoning_handler import handle_reasoning
from backend.models.camera_handler import get_camera_feed
from backend.models.image_generator import generate_image

class RequestRouter:
    """Маршрутизация запросов к appropriate обработчикам"""
    
    async def route(
        self,
        session_id: str,
        messages: List[Dict],
        image_data: Optional[Dict] = None
    ) -> Dict:
        import time
        start_time = time.time()
        
        if image_data:
            logger.info("Запрос с изображением → мультимодальный анализ")
            caption = None
            user_msg = next(
                (m for m in reversed(messages) if m["role"] == "user"),
                None
            )
            if user_msg and user_msg.get("content", "").strip():
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
                "images": [],
                "camera_image": None,
                "model_used": settings.llm_multimodal_model,
                "request_type": "multimodal_analysis",
                "duration_sec": round(duration, 2),
                "tokens": result.get("tokens", 0)
            }
        
        user_message = next(
            (m for m in reversed(messages) if m["role"] == "user"),
            {"content": ""}
        )
        user_query = user_message.get("content", "").strip()
        
        if not user_query:
            return {
                "success": False,
                "error": "Пустой запрос",
                "content": "❌ Пожалуйста, введите текст запроса.",
                "images": [],
                "camera_image": None,
                "model_used": None,
                "request_type": "empty",
                "duration_sec": 0,
                "tokens": 0
            }
        
        logger.info(f"Классификация запроса: '{user_query[:100]}...'")
        classification = await classify_request(user_query)
        logger.info(f"→ Тип: {classification.route_type}, payload: {classification.payload}")
        
        try:
            if classification.route_type == RouterResult.SIMPLE:
                duration = time.time() - start_time
                return {
                    "success": True,
                    "content": classification.payload or "✓",
                    "images": [],
                    "camera_image": None,
                    "model_used": settings.llm_chat_model,
                    "request_type": "simple",
                    "duration_sec": round(duration, 2),
                    "tokens": 0
                }
            
            elif classification.route_type == RouterResult.IMAGE_GEN:
                logger.info(f"→ Генерация изображения: '{classification.payload}'")
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
                logger.info(f"→ Запрос к камере: {classification.payload}")
                if classification.payload == ROOM_NOT_FOUND:
                    content = ROOM_NOT_FOUND
                    camera_image = None
                else:
                    cam_result = await get_camera_feed(classification.payload)
                    content = cam_result.get("message", cam_result.get("error", ""))
                    camera_image = cam_result.get("image")
                duration = time.time() - start_time
                return {
                    "success": True,
                    "content": content,
                    "images": [],
                    "camera_image": camera_image,
                    "model_used": "camera_system",
                    "request_type": "camera_request",
                    "duration_sec": round(duration, 2),
                    "tokens": 0
                }
            
            elif classification.route_type == RouterResult.REASONING:
                logger.info(f"→ Сложный запрос через reasoning модель")
                result = await handle_reasoning(
                    messages=messages,
                    session_id=session_id
                )
                duration = time.time() - start_time
                return {
                    "success": True,
                    "content": result["content"],
                    "images": [],
                    "camera_image": None,
                    "model_used": settings.llm_reasoning_model,
                    "request_type": "reasoning",
                    "duration_sec": round(duration, 2),
                    "tokens": result.get("tokens", 0)
                }
            
            else:
                logger.warning(f"Неизвестный тип маршрутизации: {classification.route_type}")
                result = await handle_reasoning(messages=messages, session_id=session_id)
                duration = time.time() - start_time
                return {
                    "success": True,
                    "content": result["content"],
                    "images": [],
                    "camera_image": None,
                    "model_used": settings.llm_reasoning_model,
                    "request_type": "reasoning_fallback",
                    "duration_sec": round(duration, 2),
                    "tokens": result.get("tokens", 0)
                }
        
        except Exception as e:
            logger.error(f"Ошибка маршрутизации: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "content": "❌ Внутренняя ошибка обработки запроса. Попробуйте ещё раз.",
                "images": [],
                "camera_image": None,
                "model_used": None,
                "request_type": classification.route_type if 'classification' in locals() else "unknown",
                "duration_sec": round(time.time() - start_time, 2),
                "tokens": 0
            }
    
    async def get_model_status(self) -> Dict:
        from backend.utils.ollama_client import ollama
        from backend.utils.a1111_client import a1111
        
        ollama_available = False
        ollama_models = []
        try:
            models = await ollama.list_models()
            ollama_available = True
            ollama_models = [m["name"] for m in models]
        except Exception as e:
            logger.warning(f"Ollama недоступен: {e}")
        
        a1111_available = False
        a1111_model_info = None
        try:
            if await a1111.is_available():
                a1111_available = True
                options = await a1111.get_options()
                a1111_model_info = options.get("sd_model_checkpoint", settings.a1111_model)
        except Exception as e:
            logger.warning(f"A1111 недоступен: {e}")
        
        return {
            "ollama": {
                "available": ollama_available,
                "url": settings.ollama_url,
                "models": [
                    settings.llm_chat_model,
                    settings.llm_multimodal_model,
                    settings.llm_reasoning_model
                ],
                "loaded": [m for m in ollama_models if any(
                    target in m for target in [
                        settings.llm_chat_model.split(":")[0],
                        settings.llm_multimodal_model.split(":")[0],
                        settings.llm_reasoning_model.split(":")[0]
                    ]
                )]
            },
            "automatic1111": {
                "available": a1111_available,
                "url": settings.automatic1111_url,
                "model": settings.a1111_model,
                "current_model": a1111_model_info
            },
            "router_model": settings.llm_chat_model,
            "timestamp": __import__('datetime').datetime.now().isoformat()
        }

router = RequestRouter()