"""
Обработка запроса через модель-маршрутизатор

Этот модуль отвечает за классификацию пользовательских запросов
и определение appropriate обработчика.
"""
import re
import json
from typing import Dict, Optional
from loguru import logger

from backend.utils.ollama_client import ollama
from backend.utils.config import settings
from backend.models.prompts import (
    ROUTER_PROMPT, 
    get_current_time_str,
    ROOM_CODES, 
    ROOM_NOT_FOUND
)


class RouterResult:
    """
    Результат классификации запроса маршрутизатором
    
    Атрибуты:
        route_type: тип маршрута (simple, image_gen, camera, reasoning, unknown)
        payload: дополнительные данные (ответ для simple, запрос для image_gen, код комнаты для camera)
        error: сообщение об ошибке если классификация не удалась
    """
    SIMPLE = "simple"           # Ответить сразу чат-моделью
    IMAGE_GEN = "image_gen"     # Генерация изображения через A1111
    CAMERA = "camera"           # Показать камеру видеонаблюдения
    REASONING = "reasoning"     # Сложный запрос через reasoning-модель
    UNKNOWN = "unknown"         # Ошибка классификации
    
    def __init__(self, 
                 route_type: str, 
                 payload: Optional[str] = None,
                 error: Optional[str] = None):
        self.route_type = route_type
        self.payload = payload
        self.error = error
    
    def __repr__(self):
        return f"RouterResult(type={self.route_type}, payload={self.payload!r}, error={self.error!r})"


async def classify_request(user_query: str) -> RouterResult:
    """
    Классификация запроса через LLM-маршрутизатор
    
    Процесс:
    1. Формирование промпта с текущим временем и запросом пользователя
    2. Запрос к чат-модели (которая также выступает маршрутизатором)
    3. Парсинг ответа и определение типа запроса
    4. Возврат RouterResult с типом и payload
    
    Args:
        user_query: текст запроса пользователя
    
    Returns:
        RouterResult с типом маршрута и данными для обработчика
    """
    current_time = get_current_time_str(settings.timezone)
    
    # Формируем промпт для маршрутизатора
    prompt = ROUTER_PROMPT.format(
        current_time_str=current_time,
        user_query=user_query
    )
    
    try:
        # Запрос к чат-модели (она же маршрутизатор)
        response = await ollama.chat(
            model=settings.llm_chat_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=settings.llm_chat_temperature,
            top_p=settings.llm_chat_top_p
        )
        
        answer = response.get("message", {}).get("content", "").strip()
        logger.debug(f"Router raw response: {answer[:300]}")
        
        # Парсим ответ маршрутизатора
        return _parse_router_response(answer, user_query)
        
    except Exception as e:
        logger.error(f"Ошибка маршрутизации через LLM: {e}")
        # Fallback: эвристическая классификация без LLM
        return _fallback_classification(user_query)


def _parse_router_response(answer: str, original_query: str) -> RouterResult:
    """
    Парсинг ответа маршрутизатора и определение типа запроса
    
    Args:
        answer: ответ модели-маршрутизатора
        original_query: исходный запрос пользователя
    
    Returns:
        RouterResult с классификацией
    """
    answer_lower = answer.lower()
    
    # 1. Простой ответ (нет специальных маркеров)
    # Маркеры: [-IMAGE-], [-CAMERA-], [-REASONING-], ⚠️
    has_marker = any(marker in answer for marker in ["[-IMAGE-]", "[-CAMERA-]", "[-REASONING-]", "⚠️"])
    
    if not has_marker:
        # Это простой ответ — возвращаем его как payload
        return RouterResult(RouterResult.SIMPLE, payload=answer)
    
    # 2. Запрос на генерацию изображения
    if "[-IMAGE-]" in answer:
        # Извлекаем запрос после маркера
        parts = answer.split("[-IMAGE-]", 1)
        prompt = parts[1].strip() if len(parts) > 1 else original_query
        return RouterResult(RouterResult.IMAGE_GEN, payload=prompt)
    
    # 3. Запрос к камере
    if "[-CAMERA-]" in answer:
        parts = answer.split("[-CAMERA-]", 1)
        room_code = parts[1].strip().lower() if len(parts) > 1 else ""
        return RouterResult(RouterResult.CAMERA, payload=room_code)
    
    # 4. Сообщение об ошибке камеры (комната не найдена)
    if "⚠️" in answer and "видеонаблюдения" in answer_lower:
        return RouterResult(RouterResult.CAMERA, payload=ROOM_NOT_FOUND)
    
    # 5. Сложный запрос
    if "[-REASONING-]" in answer:
        parts = answer.split("[-REASONING-]", 1)
        query = parts[1].strip() if len(parts) > 1 else original_query
        return RouterResult(RouterResult.REASONING, payload=query)
    
    # Не распознано — считаем сложным запросом (fallback)
    logger.warning(f"Не распознан ответ маршрутизатора: {answer[:100]}")
    return RouterResult(RouterResult.REASONING, payload=original_query)


def _fallback_classification(user_query: str) -> RouterResult:
    """
    Эвристическая классификация при ошибке LLM
    
    Использует ключевые слова для определения типа запроса.
    Менее точная чем LLM, но работает без вызова модели.
    
    Args:
        user_query: текст запроса пользователя
    
    Returns:
        RouterResult с классификацией
    """
    query_lower = user_query.lower().strip()
    
    # === Image generation keywords ===
    img_keywords = [
        "нарисуй", "создай изображение", "сгенерируй картинку", "изобрази",
        "make image", "draw", "create image", "generate picture",
        "подготовь рисунок", "сделай фотографию", "создай эскиз"
    ]
    if any(kw in query_lower for kw in img_keywords):
        return RouterResult(RouterResult.IMAGE_GEN, payload=user_query)
    
    # === Camera keywords + room names ===
    camera_keywords = ["покажи", "что в", "есть ли в", "посмотри", "камера"]
    if any(kw in query_lower for kw in camera_keywords):
        for room, code in ROOM_CODES.items():
            if room in query_lower:
                return RouterResult(RouterResult.CAMERA, payload=code)
        # Комната не найдена в списке
        return RouterResult(RouterResult.CAMERA, payload=ROOM_NOT_FOUND)
    
    # === Simple time/day queries ===
    simple_keywords = [
        "который час", "время", "день недели", "какой сегодня", 
        "год", "дата", "число", "месяц", "сколько времени"
    ]
    if any(kw in query_lower for kw in simple_keywords):
        return RouterResult(RouterResult.SIMPLE, payload=None)
    
    # === Default: reasoning ===
    # Всё остальное считаем сложным запросом
    return RouterResult(RouterResult.REASONING, payload=user_query)