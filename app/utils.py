# app/utils.py
from flask import current_app
import pytz
from datetime import datetime
import os
import base64
from io import BytesIO
from PIL import Image
import uuid
import PyPDF2
from docx import Document
from typing import List, Dict, Optional, Tuple, Any


PROMPTS_DIR = 'prompts'


def get_current_time_in_timezone(app=None) -> Optional[str]:
    """Returns current time in configured timezone in readable format."""
    if app is None:
        app = current_app
    tz = app.config.get('TIMEZONE')
    if not tz:
        app.logger.error("Timezone not configured")
        return None
    try:
        utc_now = datetime.now(pytz.UTC)
        local_time = utc_now.astimezone(tz)
        weekdays_en = {0: 'Monday', 1: 'Tuesday', 2: 'Wednesday',
                       3: 'Thursday', 4: 'Friday', 5: 'Saturday', 6: 'Sunday'}
        formatted_date = local_time.strftime('%d.%m.%Y')
        formatted_time = local_time.strftime('%H:%M:%S')
        weekday_en = weekdays_en[local_time.weekday()]
        tz_abbr = local_time.strftime('%z')
        if tz_abbr:
            tz_abbr = f"(+{int(tz_abbr[1:3])})" if tz_abbr.startswith('+') else f"({tz_abbr})"
        else:
            tz_abbr = ""
        return f"{formatted_date} {formatted_time} {weekday_en} {tz_abbr}"
    except Exception as e:
        app.logger.error(f"Error getting time: {str(e)}")
        return None


def get_current_time_in_timezone_for_db(app=None) -> str:
    """Returns current time in SQLite format using configured timezone."""
    if app is None:
        app = current_app
    tz = app.config.get('TIMEZONE')
    if not tz:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        utc_now = datetime.now(pytz.UTC)
        local_time = utc_now.astimezone(tz)
        return local_time.strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        app.logger.error(f"Error getting time for DB: {str(e)}")
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def load_prompt_template(template_name: str, lang: str = 'ru') -> Optional[str]:
    """Load prompt template from language-specific subfolder."""
    template_path = os.path.join(PROMPTS_DIR, lang, template_name)
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        if lang != 'ru':
            return load_prompt_template(template_name, 'ru')
        current_app.logger.error(f"Template not found: {template_path}")
        return None
    except Exception as e:
        current_app.logger.error(f"Error loading template {template_name}: {str(e)}")
        return None


def format_prompt(template_name: str, variables: Dict[str, Any], lang: str = 'ru') -> Optional[str]:
    """Load template and substitute variables."""
    template = load_prompt_template(template_name, lang)
    if not template:
        return None
    try:
        return template.format(**variables)
    except KeyError as e:
        current_app.logger.error(f"Missing variable in template {template_name}: {e}")
        return None
    except Exception as e:
        current_app.logger.error(f"Error formatting template {template_name}: {str(e)}")
        return None


def resize_image_if_needed(
    file_data: str,
    file_type: str,
    file_name: str,
    max_width: int,
    max_height: int,
    quality: int = 85
) -> Tuple[str, str, str, bool, Optional[Tuple[int, int]], Optional[Tuple[int, int]]]:
    """
    Resize image if it exceeds max dimensions.
    Returns (new_file_data, new_file_type, new_file_name, resized_flag, original_dimensions, new_dimensions)
    """
    try:
        image_bytes = base64.b64decode(file_data)
        img = Image.open(BytesIO(image_bytes))
        original_width, original_height = img.size

        if original_width <= max_width and original_height <= max_height:
            return file_data, file_type, file_name, False, None, None

        ratio = min(max_width / original_width, max_height / original_height)
        new_width = int(original_width * ratio)
        new_height = int(original_height * ratio)

        img_resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        if img_resized.mode in ('RGBA', 'LA', 'P'):
            rgb_img = Image.new('RGB', img_resized.size, (255, 255, 255))
            rgb_img.paste(img_resized, mask=img_resized.split()[-1] if img_resized.mode == 'RGBA' else None)
            img_resized = rgb_img

        output = BytesIO()
        img_resized.save(output, format='JPEG', quality=quality, optimize=True)
        output_bytes = output.getvalue()
        new_file_data = base64.b64encode(output_bytes).decode('utf-8')
        new_file_type = 'image/jpeg'
        base, _ = os.path.splitext(file_name)
        new_file_name = base + '.jpg'

        return new_file_data, new_file_type, new_file_name, True, (original_width, original_height), (new_width, new_height)
    except Exception as e:
        current_app.logger.error(f"Error resizing image: {str(e)}")
        return file_data, file_type, file_name, False, None, None


def save_uploaded_file(file_data: str, filename: str, session_id: str, upload_folder: str) -> Optional[str]:
    """Save a base64 encoded file to disk. Returns relative path."""
    if not file_data:
        return None
    try:
        file_bytes = base64.b64decode(file_data)
    except Exception as e:
        current_app.logger.error(f"Failed to decode base64 file data: {e}")
        return None

    session_folder = os.path.join(upload_folder, session_id)
    os.makedirs(session_folder, exist_ok=True)

    ext = os.path.splitext(filename)[1] if filename else '.bin'
    if not ext:
        ext = '.bin'
    unique_name = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(session_folder, unique_name)

    try:
        with open(file_path, 'wb') as f:
            f.write(file_bytes)
        current_app.logger.info(f"Saved uploaded file to {file_path}")
        return os.path.join(session_id, unique_name)
    except Exception as e:
        current_app.logger.error(f"Failed to save file {file_path}: {e}")
        return None


def extract_text_from_file(file_path: str) -> Optional[str]:
    """Extract text from a file (PDF, DOCX, TXT)."""
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == '.txt':
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        elif ext == '.pdf':
            text = ''
            with open(file_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + '\n'
            return text.strip()
        elif ext == '.docx':
            doc = Document(file_path)
            return '\n'.join([para.text for para in doc.paragraphs])
        else:
            return None
    except Exception as e:
        current_app.logger.error(f"Error extracting text from {file_path}: {e}")
        return None


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """Split text into overlapping chunks of approximately chunk_size words."""
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = ' '.join(words[i:i+chunk_size])
        if chunk:
            chunks.append(chunk)
        i += chunk_size - overlap
    return chunks


def estimate_tokens(text: str, token_chars: int = 3) -> int:
    """Estimate tokens based on characters per token."""
    return len(text) // token_chars + 1


def build_context_prompt(history: List[Dict[str, str]], lang: str = 'ru') -> str:
    """Format conversation history into a string."""
    if not history:
        return ""
    lines = []
    for msg in history:
        role = "User" if msg['role'] == 'user' else "Assistant"
        lines.append(f"{role}: {msg['content']}")
    return "\n".join(lines)