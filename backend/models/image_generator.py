"""
Генерация изображений через Automatic1111 API

Этот модуль отвечает за:
1. Подготовку промпта для Stable Diffusion через мультимодальную модель
2. Вызов A1111 txt2img API
3. Обработку результата и возврат base64-изображения
"""
import base64
from loguru import logger

from backend.utils.a1111_client import a1111
from backend.utils.config import settings
from backend.models.multimodal_handler import prepare_sd_prompt


async def generate_image(user_query: str, session_id: str) -> dict:
    """
    Генерация изображения по запросу пользователя
    
    Процесс:
    1. Подготовка SD-промпта через мультимодальную модель (prepare_sd_prompt)
    2. Вызов A1111 txt2img API с подготовленными параметрами
    3. Возврат base64-изображения и метаданных
    
    Args:
        user_query: запрос пользователя на русском языке
        session_id: идентификатор сессии для логирования
    
    Returns:
        dict с результатом генерации:
        {
            "success": bool,
            "content": str,  # текстовый ответ пользователю
            "images": List[str],  # список base64-изображений
            "metadata": dict,  # дополнительные данные
            "error": Optional[str]  # сообщение об ошибке если есть
        }
    """
    logger.info(f"Запрос на генерацию изображения: '{user_query}'")
    
    # 1. Подготовка промпта для Stable Diffusion
    logger.debug("Подготовка SD-промпта через мультимодальную модель...")
    sd_params = await prepare_sd_prompt(user_query)
    
    if not sd_params.get("prompt"):
        return {
            "success": False,
            "error": "Не удалось подготовить промпт для генерации",
            "content": "❌ Ошибка подготовки запроса к генератору изображений.",
            "images": [],
            "metadata": {}
        }
    
    # 2. Генерация через Automatic1111
    logger.debug(f"Вызов A1111: prompt='{sd_params['prompt'][:50]}...', steps={sd_params['steps']}")
    
    try:
        result = await a1111.txt2img(
            prompt=sd_params["prompt"],
            negative_prompt=sd_params["negative_prompt"],
            steps=sd_params["steps"],
            cfg_scale=sd_params["cfg_scale"],
            width=sd_params["width"],
            height=sd_params["height"],
            sampler=sd_params["sampler_name"],
            model=settings.a1111_model
        )
        
        image_base64 = result.get("image")
        if not image_base64:
            return {
                "success": False,
                "error": "A1111 не вернул изображение",
                "content": "❌ Ошибка генерации изображения: пустой ответ от генератора.",
                "images": [],
                "metadata": {"params": sd_params}
            }
        
        logger.info(f"Изображение сгенерировано: {len(image_base64)} bytes base64")
        
        return {
            "success": True,
            "content": f"✅ Изображение сгенерировано по запросу: \"{user_query}\"",
            "images": [image_base64],
            "metadata": {
                "prompt": sd_params["prompt"][:100] + ("..." if len(sd_params["prompt"]) > 100 else ""),
                "negative_prompt": sd_params["negative_prompt"][:50] + "...",
                "model": settings.a1111_model,
                "steps": sd_params["steps"],
                "cfg_scale": sd_params["cfg_scale"],
                "dimensions": f"{sd_params['width']}x{sd_params['height']}",
                "sampler": sd_params["sampler_name"]
            }
        }
        
    except Exception as e:
        logger.error(f"Ошибка генерации изображения: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "content": f"❌ Ошибка генерации: {e}",
            "images": [],
            "metadata": {"params": sd_params}
        }