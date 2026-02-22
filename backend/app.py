"""
Основное FastAPI приложение ИИ Локальный
Полностью автономная версия для локального развёртывания
"""
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
import aiofiles
import os
import uuid
import base64
import magic
import json
from loguru import logger
import sys
from typing import List, Optional, Dict, Any
from datetime import datetime
from pathlib import Path

from backend.utils.config import settings
from backend.utils.file_utils import validate_image, save_upload, load_file
from backend.router import router as request_router

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================
logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
)

# ==================== ИНИЦИАЛИЗАЦИЯ ПРИЛОЖЕНИЯ ====================
app = FastAPI(
    title="ИИ Локальный",
    version="3.3",
    description="Локальный чат-интерфейс для ИИ-моделей с маршрутизацией запросов",
    docs_url=None,  # Отключаем Swagger для безопасности в production
    redoc_url=None
)

# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Для локального использования
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

# ==================== МОНТИРОВАНИЕ ФРОНТЕНДА ====================
from pathlib import Path

# Путь к фронтенду внутри контейнера: /app/frontend
# app.py находится в /app/backend/app.py
# Поэтому: parent = /app/backend, parent.parent = /app
frontend_path = Path(__file__).resolve().parent.parent / "frontend"

# Fallback: явный путь для Docker
if not frontend_path.exists():
    frontend_path = Path("/app/frontend")

logger.info(f"📁 Frontend path: {frontend_path}")
logger.info(f"📁 Frontend exists: {frontend_path.exists()}")
if frontend_path.exists():
    index_path = frontend_path / "index.html"
    logger.info(f"📄 index.html exists: {index_path.exists()}")


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Сервинг фронтенда"""
    index_path = frontend_path / "index.html"
    
    if index_path.exists():
        try:
            async with aiofiles.open(index_path, "r", encoding="utf-8") as f:
                content = await f.read()
                # Подстановка footer из настроек
                content = content.replace(
                    'document.currentScript.dataset.footer || \'ИИ Локальный v3.3 (с) 2026 Барсуков Валерий\'',
                    f'"{settings.footer_text}"'
                )
                return HTMLResponse(content=content)
        except Exception as e:
            logger.error(f"Ошибка чтения index.html: {e}")
            return HTMLResponse(content=f"<h1>Ошибка: {e}</h1>", status_code=500)
    
    logger.error(f"❌ Frontend not found at {index_path}")
    return HTMLResponse(
        content=f"""
        <html><body style="font-family:sans-serif;padding:2rem">
            <h1>⚠️ Frontend not found</h1>
            <p>Путь: <code>{index_path}</code></p>
            <p>Существует: {index_path.exists()}</p>
            <h3>Проверьте:</h3>
            <pre>docker-compose exec ai-local-app ls -la /app/frontend/</pre>
        </body></html>
        """,
        status_code=404
    )

# ==================== ХЕЛПЕРЫ ДЛЯ СЕССИЙ ====================
def get_session_path(session_id: str) -> str:
    """Получение пути к файлу сессии"""
    return os.path.join(settings.sessions_path, f"{session_id}.json")


async def load_session(session_id: str) -> Optional[dict]:
    """Загрузка сессии из хранилища"""
    path = get_session_path(session_id)
    if os.path.exists(path):
        try:
            async with aiofiles.open(path, 'r', encoding='utf-8') as f:
                content = await f.read()
                return json.loads(content)
        except Exception as e:
            logger.error(f"Ошибка загрузки сессии {session_id}: {e}")
    return None


async def save_session(session_id: str, data: dict) -> bool:
    """Сохранение сессии"""
    try:
        path = get_session_path(session_id)
        async with aiofiles.open(path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения сессии {session_id}: {e}")
        return False


async def cleanup_old_sessions(max_age_hours: int = 168):  # 7 дней по умолчанию
    """Очистка старых сессий (фоновая задача)"""
    try:
        cutoff = datetime.now().timestamp() - (max_age_hours * 3600)
        for filename in os.listdir(settings.sessions_path):
            if filename.endswith('.json'):
                filepath = os.path.join(settings.sessions_path, filename)
                if os.path.getmtime(filepath) < cutoff:
                    os.remove(filepath)
                    logger.info(f"Удалена старая сессия: {filename}")
    except Exception as e:
        logger.error(f"Ошибка очистки сессий: {e}")


# ==================== API ENDPOINTS ====================

@app.get("/health")
async def health_check():
    """
    Проверка здоровья приложения
    Используется для healthcheck в Docker
    """
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "version": "3.3",
        "timezone": settings.timezone
    }


@app.get("/api/models/status")
async def get_models_status():
    """
    Статус доступных моделей и сервисов
    """
    return await request_router.get_model_status()


@app.post("/api/chat")
async def chat_endpoint(
    request: Request,
    session_id: str = Form(...),
    message: str = Form(...),
    file: Optional[UploadFile] = File(None)
):
    """
    Обработка чат-запроса
    
    Поддерживает:
    - Текстовые сообщения
    - Сообщения с изображением (анализ мультимодальной моделью)
    - Запросы на генерацию изображений (через Automatic1111)
    - Запросы к камерам видеонаблюдения
    
    Returns:
        JSON с ответом ассистента и метаданными
    """
    import time
    start_time = time.time()
    
    # Валидация и создание сессии
    if not session_id or session_id == "null":
        session_id = str(uuid.uuid4())
    
    # Загрузка или создание сессии
    session = await load_session(session_id)
    if not session:
        session = {
            "id": session_id,
            "created": datetime.now().isoformat(),
            "updated": datetime.now().isoformat(),
            "messages": [],
            "title": message[:50] + "..." if len(message) > 50 else message,
            "metadata": {}
        }
    
    # Обработка файла если есть
    image_data = None
    if file and file.filename and file.content_type:
        try:
            content = await file.read()
            mime_type = file.content_type
            
            # Валидация изображения
            is_valid, error = validate_image(content, mime_type)
            if not is_valid:
                raise HTTPException(status_code=400, detail=error)
            
            # Сохранение файла
            filename = await save_upload(content, file.filename, session_id)
            
            # Кодирование для отправки в модель
            image_data = {
                "filename": filename,
                "original_name": file.filename,
                "mime_type": mime_type,
                "size": len(content),
                "base64": base64.b64encode(content).decode('utf-8')
            }
            
            logger.info(f"Файл загружен: {filename} ({len(content)} bytes)")
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Ошибка обработки файла: {e}")
            raise HTTPException(status_code=500, detail=f"Ошибка обработки файла: {e}")
    
    # Добавляем сообщение пользователя в историю
    user_message = {
        "role": "user",
        "content": message.strip(),
        "timestamp": datetime.now().isoformat(),
        "attachment": image_data["filename"] if image_data else None
    }
    session["messages"].append(user_message)
    session["updated"] = datetime.now().isoformat()
    
    # === МАРШРУТИЗАЦИЯ И ОБРАБОТКА ЗАПРОСА ===
    result = await request_router.route(
        session_id=session_id,
        messages=session["messages"],
        image_data=image_data
    )
    
    # Обработка ошибки маршрутизации
    if not result.get("success", False):
        error_message = {
            "role": "assistant",
            "content": f"❌ Ошибка: {result.get('error', 'Неизвестная ошибка')}",
            "timestamp": datetime.now().isoformat(),
            "error": True,
            "metadata": {
                "model": None,
                "request_type": result.get("request_type", "unknown"),
                "duration": round(time.time() - start_time, 2)
            }
        }
        session["messages"].append(error_message)
        await save_session(session_id, session)
        
        return JSONResponse(
            status_code=500 if result.get("error") else 200,
            content={
                "session_id": session_id,
                "message": error_message,
                "error": result.get("error")
            }
        )
    
    # Формирование ответа ассистента
    assistant_message = {
        "role": "assistant",
        "content": result.get("content", ""),
        "timestamp": datetime.now().isoformat(),
        "metadata": {
            "model": result.get("model_used"),
            "request_type": result.get("request_type"),
            "duration": result.get("duration_sec", round(time.time() - start_time, 2)),
            "tokens": result.get("tokens", 0)
        }
    }
    
    # Добавление изображений если есть (генерация или анализ)
    if result.get("images"):
        assistant_message["images"] = result["images"]
    
    # Добавление изображения камеры если есть
    if result.get("camera_image"):
        assistant_message["camera_image"] = result["camera_image"]
    
    session["messages"].append(assistant_message)
    
    # Сохранение сессии
    await save_session(session_id, session)
    
    # Логирование
    duration = time.time() - start_time
    logger.info(
        f"Запрос обработан: session={session_id[:8]}, "
        f"type={result.get('request_type')}, "
        f"model={result.get('model_used', 'N/A')}, "
        f"duration={duration:.2f}s"
    )
    
    return {
        "session_id": session_id,
        "message": assistant_message,
        "metadata": {
            "model": result.get("model_used"),
            "request_type": result.get("request_type"),
            "duration": round(duration, 2)
        }
    }


@app.get("/api/sessions")
async def list_sessions(limit: int = 50):
    """
    Список всех сессий
    
    Args:
        limit: максимальное количество сессий для возврата
    """
    sessions = []
    
    try:
        for filename in os.listdir(settings.sessions_path):
            if filename.endswith('.json'):
                filepath = os.path.join(settings.sessions_path, filename)
                try:
                    async with aiofiles.open(filepath, 'r', encoding='utf-8') as f:
                        data = json.loads(await f.read())
                        sessions.append({
                            "id": data.get("id"),
                            "title": data.get("title", "Без названия"),
                            "created": data.get("created"),
                            "updated": data.get("updated"),
                            "messages_count": len(data.get("messages", []))
                        })
                except Exception as e:
                    logger.warning(f"Ошибка чтения сессии {filename}: {e}")
                    continue
        
        # Сортировка по дате обновления (новые первые)
        sessions.sort(key=lambda x: x.get("updated", x.get("created", "")), reverse=True)
        
        return sessions[:limit]
        
    except Exception as e:
        logger.error(f"Ошибка получения списка сессий: {e}")
        raise HTTPException(status_code=500, detail="Ошибка получения сессий")


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """Получение полной сессии по ID"""
    session = await load_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    return session


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """Удаление сессии и связанных файлов"""
    path = get_session_path(session_id)
    
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    
    try:
        # Удаление файла сессии
        os.remove(path)
        
        # Удаление загруженных файлов сессии
        uploads_dir = os.path.join(settings.uploads_path, session_id)
        if os.path.exists(uploads_dir):
            import shutil
            shutil.rmtree(uploads_dir)
        
        logger.info(f"Сессия удалена: {session_id}")
        return {"success": True, "message": "Сессия удалена"}
        
    except Exception as e:
        logger.error(f"Ошибка удаления сессии {session_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка удаления: {e}")


@app.get("/api/files/{session_id}/{filename}")
async def get_file(session_id: str, filename: str):
    """
    Получение загруженного файла
    
    Args:
        session_id: идентификатор сессии
        filename: имя файла
    """
    # Проверка на path traversal
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Некорректное имя файла")
    
    filepath = os.path.join(settings.uploads_path, session_id, filename)
    
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Файл не найден")
    
    # Определение MIME-типа
    mime_type = magic.from_file(filepath, mime=True)
    
    return FileResponse(
        filepath,
        filename=filename,
        media_type=mime_type,
        headers={"Cache-Control": "public, max-age=3600"}
    )


@app.post("/api/export/{session_id}")
async def export_session(session_id: str):
    """
    Экспорт сессии в JSON файл для скачивания
    """
    session = await load_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    
    # Удаление base64 изображений из экспорта для уменьшения размера
    export_session = session.copy()
    for msg in export_session.get("messages", []):
        if "images" in msg:
            msg["images"] = ["[base64_image_data_removed]"]
    
    filename = f"session_{session_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    filepath = os.path.join(settings.cache_path, filename)
    
    try:
        async with aiofiles.open(filepath, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(export_session, ensure_ascii=False, indent=2))
        
        return FileResponse(
            filepath,
            filename=filename,
            media_type='application/json',
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except Exception as e:
        logger.error(f"Ошибка экспорта сессии: {e}")
        raise HTTPException(status_code=500, detail="Ошибка экспорта")


@app.post("/api/import")
async def import_session(file: UploadFile = File(...)):
    """
    Импорт сессии из JSON файла
    
    Args:
        file: JSON файл с экспортированной сессией
    """
    if not file.filename.endswith('.json'):
        raise HTTPException(status_code=400, detail="Только JSON файлы")
    
    try:
        content = await file.read()
        session = json.loads(content.decode('utf-8'))
        
        # Валидация структуры
        if "id" not in session or "messages" not in session:
            raise ValueError("Некорректная структура сессии")
        
        # Генерация нового ID чтобы избежать конфликтов
        old_id = session["id"]
        session["id"] = str(uuid.uuid4())
        session["imported_from"] = old_id
        session["imported_at"] = datetime.now().isoformat()
        
        # Сохранение
        await save_session(session["id"], session)
        
        logger.info(f"Сессия импортирована: {old_id} -> {session['id']}")
        
        return {
            "success": True,
            "session_id": session["id"],
            "message": "Сессия импортирована"
        }
        
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Некорректный JSON")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Ошибка импорта сессии: {e}")
        raise HTTPException(status_code=500, detail=f"Ошибка импорта: {e}")


@app.get("/api/stats")
async def get_stats():
    """Статистика использования"""
    try:
        total_sessions = 0
        total_messages = 0
        total_files = 0
        total_size = 0
        
        for filename in os.listdir(settings.sessions_path):
            if filename.endswith('.json'):
                total_sessions += 1
                filepath = os.path.join(settings.sessions_path, filename)
                try:
                    async with aiofiles.open(filepath, 'r', encoding='utf-8') as f:
                        data = json.loads(await f.read())
                        total_messages += len(data.get("messages", []))
                except:
                    pass
        
        for root, dirs, files in os.walk(settings.uploads_path):
            for f in files:
                total_files += 1
                filepath = os.path.join(root, f)
                total_size += os.path.getsize(filepath)
        
        return {
            "sessions": total_sessions,
            "messages": total_messages,
            "files": total_files,
            "storage_mb": round(total_size / (1024 * 1024), 2),
            "timezone": settings.timezone
        }
        
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        return {"error": str(e)}


# ==================== ФОНОВЫЕ ЗАДАЧИ ====================
@app.on_event("startup")
async def startup_event():
    """Инициализация при запуске"""
    logger.info("🚀 Запуск ИИ Локальный v3.3")
    logger.info(f"📁 Storage: {settings.storage_path}")
    logger.info(f"🌐 Ollama: {settings.ollama_url}")
    logger.info(f"🎨 A1111: {settings.automatic1111_url}")
    
    # Создание директорий
    os.makedirs(settings.uploads_path, exist_ok=True)
    os.makedirs(settings.sessions_path, exist_ok=True)
    os.makedirs(settings.cache_path, exist_ok=True)
    
    # Запуск фоновой очистки (раз в час)
    # В production лучше использовать Celery или аналог
    # asyncio.create_task(periodic_cleanup())


# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=settings.app_port,
        reload=False,
        log_level="info",
        access_log=True
    )