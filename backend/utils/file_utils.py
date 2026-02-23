"""
Утилиты для работы с файлами и изображениями
"""
import os
import uuid
import base64
import magic
import aiofiles
import json
from typing import Dict, Any, Optional, Tuple, List
from PIL import Image
from io import BytesIO
from loguru import logger
from backend.utils.config import settings

ALLOWED_IMAGE_MIMES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/gif"
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

def validate_image(content: bytes, mime_type: str) -> Tuple[bool, Optional[str]]:
    if mime_type not in ALLOWED_IMAGE_MIMES:
        return False, f"Неподдерживаемый формат: {mime_type}"
    
    if len(content) > settings.max_image_size_bytes:
        max_mb = settings.max_image_size_mb
        actual_mb = len(content) / (1024 * 1024)
        return False, f"Файл слишком большой: {actual_mb:.1f} МБ (макс. {max_mb} МБ)"
    
    try:
        img = Image.open(BytesIO(content))
        img.verify()
        img = Image.open(BytesIO(content))
        width, height = img.size
        if width > settings.max_image_width or height > settings.max_image_height:
            return False, f"Изображение слишком большое: {width}x{height} (макс. {settings.max_image_width}x{settings.max_image_height})"
        return True, None
    except Exception as e:
        logger.error(f"Ошибка валидации изображения: {e}")
        return False, f"Ошибка чтения изображения: {e}"

def resize_image(content: bytes, max_width: int, max_height: int) -> Tuple[bytes, str]:
    try:
        img = Image.open(BytesIO(content))
        if img.mode in ("RGBA", "P", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
            img = background
        img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        output = BytesIO()
        format_name = "JPEG" if img.format in ("JPEG", "JPG") else img.format or "PNG"
        if format_name == "JPEG":
            img = img.convert("RGB")
            img.save(output, format="JPEG", quality=90, optimize=True)
            mime_type = "image/jpeg"
        else:
            img.save(output, format=format_name, optimize=True)
            mime_type = f"image/{format_name.lower()}"
        return output.getvalue(), mime_type
    except Exception as e:
        logger.error(f"Ошибка изменения размера изображения: {e}")
        return content, "image/png"

def image_to_base64(content: bytes, mime_type: str) -> str:
    return base64.b64encode(content).decode("utf-8")

async def save_upload(
    content: bytes,
    original_filename: str,
    session_id: str,
    resize: bool = True
) -> str:
    session_dir = os.path.join(settings.uploads_path, session_id)
    os.makedirs(session_dir, exist_ok=True)
    
    ext = os.path.splitext(original_filename)[1].lower()
    if ext not in IMAGE_EXTENSIONS:
        ext = ".bin"
    
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(session_dir, filename)
    
    if resize and ext in IMAGE_EXTENSIONS:
        mime = magic.from_buffer(content, mime=True)
        content, _ = resize_image(
            content,
            settings.max_image_width,
            settings.max_image_height
        )
    
    async with aiofiles.open(filepath, "wb") as f:
        await f.write(content)
    
    logger.info(f"Файл сохранён: {filepath} ({len(content)} bytes)")
    return filename

async def load_file(session_id: str, filename: str) -> Optional[bytes]:
    filepath = os.path.join(settings.uploads_path, session_id, filename)
    if not os.path.exists(filepath):
        logger.warning(f"Файл не найден: {filepath}")
        return None
    async with aiofiles.open(filepath, "rb") as f:
        return await f.read()

async def delete_file(session_id: str, filename: str) -> bool:
    filepath = os.path.join(settings.uploads_path, session_id, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
        logger.info(f"Файл удалён: {filepath}")
        return True
    return False

def get_file_info(filepath: str) -> Dict[str, Any]:
    if not os.path.exists(filepath):
        return {}
    stat = os.stat(filepath)
    mime = magic.from_file(filepath, mime=True)
    return {
        "filename": os.path.basename(filepath),
        "size": stat.st_size,
        "size_mb": round(stat.st_size / (1024 * 1024), 2),
        "mime_type": mime,
        "modified": stat.st_mtime,
        "is_image": mime.startswith("image/")
    }