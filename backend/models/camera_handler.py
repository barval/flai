"""
Обработка запросов к системе видеонаблюдения

Этот модуль отвечает за:
1. Получение изображения с камеры по коду комнаты
2. Интеграцию с реальной системой видеонаблюдения (RTSP, ONVIF, API NVR)

В демо-режиме возвращает заглушку.
"""
import base64
from typing import Optional, Dict
from loguru import logger

from .prompts import ROOM_NOT_FOUND, ROOM_CODES_REVERSE


async def get_camera_feed(room_code: str) -> Dict:
    """
    Получение изображения с камеры по коду комнаты
    
    В реальной системе здесь будет запрос к API видеонаблюдения.
    Для демо возвращаем заглушку с информацией о комнате.
    
    Args:
        room_code: код комнаты (tam, pri, kor, spa, kab, det, gos, kuh, bal)
    
    Returns:
        dict с результатом:
        {
            "success": bool,
            "room_code": str,
            "room_name": str,
            "image": Optional[str],  # base64 изображения или None
            "message": str,
            "error": Optional[str]
        }
    """
    # Проверка на ошибку из маршрутизатора
    if room_code == ROOM_NOT_FOUND or room_code.startswith("⚠️"):
        return {
            "success": False,
            "error": ROOM_NOT_FOUND,
            "room_code": None,
            "room_name": None,
            "image": None,
            "message": ROOM_NOT_FOUND
        }
    
    # Валидация кода комнаты
    if room_code not in ROOM_CODES_REVERSE:
        error_msg = f"Неизвестный код комнаты: {room_code}"
        logger.warning(error_msg)
        return {
            "success": False,
            "error": error_msg,
            "room_code": room_code,
            "room_name": None,
            "image": None,
            "message": f"❌ {error_msg}"
        }
    
    # Получение названия комнаты для отображения
    room_name = ROOM_CODES_REVERSE.get(room_code, room_code)
    
    # === ИНТЕГРАЦИЯ С СИСТЕМОЙ ВИДЕОНАБЛЮДЕНИЯ ===
    # Раскомментируйте и настройте под вашу систему:
    
    # Пример для RTSP-камер с OpenCV:
    # import cv2
    # import numpy as np
    # 
    # rtsp_url = f"rtsp://admin:password@192.168.1.100/{room_code}.stream"
    # cap = cv2.VideoCapture(rtsp_url)
    # 
    # if not cap.isOpened():
    #     return {
    #         "success": False,
    #         "error": "Нет соединения с камерой",
    #         "room_code": room_code,
    #         "room_name": room_name,
    #         "image": None,
    #         "message": f"❌ Нет сигнала с камеры '{room_name}'"
    #     }
    # 
    # ret, frame = cap.read()
    # cap.release()
    # 
    # if ret:
    #     # Конвертация в base64
    #     _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    #     image_base64 = base64.b64encode(buffer).decode('utf-8')
    #     return {
    #         "success": True,
    #         "room_code": room_code,
    #         "room_name": room_name,
    #         "image": image_base64,
    #         "message": f"📷 Камера '{room_name}': изображение получено"
    #     }
    # else:
    #     return {
    #         "success": False,
    #         "error": "Не удалось получить кадр",
    #         "room_code": room_code,
    #         "room_name": room_name,
    #         "image": None,
    #         "message": f"❌ Ошибка получения изображения с камеры '{room_name}'"
    #     }
    
    # === ДЕМО-РЕЖИМ: заглушка ===
    # В демо возвращаем только сообщение без изображения
    logger.info(f"Запрос к камере (демо): {room_name} ({room_code})")
    
    return {
        "success": True,
        "room_code": room_code,
        "room_name": room_name,
        "image": None,  # В демо: None, в продакшене: base64 изображения
        "message": f"📷 Камера '{room_name}': изображение получено (демо-режим)"
    }


async def list_cameras() -> Dict[str, str]:
    """
    Получение списка доступных камер
    
    Returns:
        dict {room_code: room_name}
    """
    return ROOM_CODES_REVERSE.copy()