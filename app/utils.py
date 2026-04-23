# app/utils.py
from flask import current_app
import pytz
from datetime import datetime
import os
import re
import base64
from io import BytesIO
from PIL import Image
import uuid
import PyPDF2
from docx import Document
from typing import List, Dict, Optional, Tuple, Any

PROMPTS_DIR = 'prompts'

# ── Shared error translations for sd.cpp module ──
SD_ERROR_TRANSLATIONS = {
    'Image generation failed': 'Image generation failed. Try again later.',
    'Image generation produced empty output': 'Image generation produced empty output.',
    'sd-wrapper returned no image data': 'sd-wrapper returned no image data.',
    'sd-wrapper returned no image': 'sd-wrapper returned no image.',
    'sd-cli timeout': 'Image generation timeout ({timeout}s)',
    'Image editing failed': 'Image editing failed. Try again later.',
    'Image editing produced empty output': 'Image editing produced empty output.',
    'No edit prompt provided': 'No editing instructions provided.',
    'No source image provided': 'No source image provided.',
    'sd-cli edit timeout': 'Image editing timeout ({timeout}s)',
}


def translate_sd_error(error_key: str, translate_func, lang: str = 'ru', **kwargs) -> str:
    """Translate sd.cpp error messages using Flask-Babel.

    Args:
        error_key: The English error message key
        translate_func: The module's self._() translation function
        lang: Language code
        **kwargs: Format arguments for the message
    """
    template = SD_ERROR_TRANSLATIONS.get(error_key)
    if template:
        return translate_func(template, lang, **kwargs)
    return translate_func('Image generation error. Check logs for details.', lang)


def extract_quantization(filename: str) -> str:
    """Extract quantization type from GGUF filename.

    Args:
        filename: Model filename like 'model-Q4_K_M.gguf'

    Returns:
        Quantization type like 'Q4_K_M' or 'Unknown'
    """
    qtypes = [
        'Q2_K', 'Q3_K_S', 'Q3_K_M', 'Q3_K_L',
        'Q4_0', 'Q4_K_S', 'Q4_K_M',
        'Q5_0', 'Q5_K_S', 'Q5_K_M',
        'Q6_K', 'Q8_0',
        'IQ2_XXS', 'IQ2_XS', 'IQ2_S', 'IQ2_M',
        'IQ3_XXS', 'IQ3_S', 'IQ3_M',
        'IQ4_XS', 'IQ4_NL',
        'F16', 'F32', 'BF16',
        'MXFP4', 'MXFP6', 'MXFP8',
        'A4B', 'A2B'
    ]
    fname_upper = filename.upper()
    for qt in qtypes:
        if qt in fname_upper:
            return qt
    return 'Unknown'


# Token estimation coefficients for different languages and model types
# Format: (model_type, language) -> characters per token
TOKEN_COEFFICIENTS = {
    ('chat', 'ru'): 2.2,
    ('chat', 'en'): 3.5,
    ('reasoning', 'ru'): 2.0,
    ('reasoning', 'en'): 3.2,
    ('multimodal', 'ru'): 2.5,
    ('multimodal', 'en'): 3.3,
    ('embedding', 'ru'): 2.0,
    ('embedding', 'en'): 3.0,
}

# Safety margin to prevent context overflow (use only 85% of calculated capacity)
SAFETY_MARGIN = 0.85

# Overhead for template text and system instructions
TEMPLATE_OVERHEAD = 800


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
        # Get language from session, fallback to 'ru'
        lang = 'ru'
        try:
            from flask import session
            lang = session.get('language', 'ru')
        except Exception:
            pass
        # Localized weekday names
        weekdays = {
            'ru': {0: 'Понедельник', 1: 'Вторник', 2: 'Среда',
                   3: 'Четверг', 4: 'Пятница', 5: 'Суббота', 6: 'Воскресенье'},
            'en': {0: 'Monday', 1: 'Tuesday', 2: 'Wednesday',
                   3: 'Thursday', 4: 'Friday', 5: 'Saturday', 6: 'Sunday'}
        }
        # Localized month names to prevent model from confusing date with time (e.g. 11.04 -> 11:04)
        months = {
            'ru': {1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля', 5: 'мая', 6: 'июня',
                   7: 'июля', 8: 'августа', 9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'},
            'en': {1: 'January', 2: 'February', 3: 'March', 4: 'April', 5: 'May', 6: 'June',
                   7: 'July', 8: 'August', 9: 'September', 10: 'October', 11: 'November', 12: 'December'}
        }
        weekday_names = weekdays.get(lang, weekdays['ru'])
        month_names = months.get(lang, months['ru'])

        # Format: "11 April 2026, time 01:30:41 Saturday (UTC+3)"
        # Text month prevents model from confusing 11.04 with time 11:04.
        formatted_date = f"{local_time.day} {month_names[local_time.month]} {local_time.year}"
        formatted_time = local_time.strftime('%H:%M:%S')
        weekday_name = weekday_names[local_time.weekday()]

        tz_offset = local_time.strftime('%z')
        if tz_offset:
            sign = '+' if tz_offset.startswith('+') else ''
            hours = int(tz_offset[1:3])
            tz_abbr = f"(UTC{sign}{hours})"
        else:
            tz_abbr = ""
        time_word = "время" if lang == 'ru' else "time"
        return f"{formatted_date}, {time_word} {formatted_time} {weekday_name} {tz_abbr}"
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


def save_uploaded_file(file_data: str, filename: str, session_id: str, upload_folder: str,
                       user_id: str = None) -> Optional[str]:
    """Save a base64 encoded file to disk. Returns relative path.
    
    If user_id is provided, also updates the user's storage counter (O(1)).
    """
    if not file_data:
        return None

    # Security: validate session_id to prevent path traversal
    if not session_id or '..' in session_id or '/' in session_id or '\\' in session_id:
        current_app.logger.error(f"Invalid session_id: {session_id}")
        return None
    
    # Security: validate session_id is a valid UUID format
    try:
        uuid.UUID(session_id, version=4)
    except ValueError:
        current_app.logger.error(f"Invalid UUID format for session_id: {session_id}")
        return None

    try:
        file_bytes = base64.b64decode(file_data)
    except Exception as e:
        current_app.logger.error(f"Failed to decode base64 file data: {e}")
        return None

    session_folder = os.path.join(upload_folder, session_id)
    os.makedirs(session_folder, exist_ok=True)
    
    # Security: sanitize filename - extract only the extension, generate unique name
    ext = os.path.splitext(filename)[1] if filename else '.bin'
    if not ext:
        ext = '.bin'
    # Remove any potentially dangerous characters from extension
    max_ext_length = current_app.config.get('MAX_EXTENSION_LENGTH', 10)
    ext = ext[:max_ext_length]  # Limit extension length
    ext = ''.join(c for c in ext if c.isalnum() or c == '.')
    if not ext.startswith('.'):
        ext = '.' + ext
    
    unique_name = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(session_folder, unique_name)
    
    # Security: verify the resolved path is within upload folder
    abs_upload_folder = os.path.abspath(upload_folder)
    abs_file_path = os.path.abspath(file_path)
    if not abs_file_path.startswith(abs_upload_folder + os.sep):
        current_app.logger.error(f"Path traversal attempt blocked: {file_path}")
        return None
    
    try:
        with open(file_path, 'wb') as f:
            f.write(file_bytes)
        current_app.logger.info(f"Saved uploaded file to {file_path}")

        # Update user's storage counter (O(1)) if user_id is provided
        if user_id:
            from . import db
            file_size = len(file_bytes)
            db.update_user_storage(user_id, file_size)

        return os.path.join(session_id, unique_name)
    except Exception as e:
        current_app.logger.error(f"Failed to save file {file_path}: {e}")
        return None


def extract_text_from_file(file_path: str) -> Optional[str]:
    """Extract text from a file (PDF, DOCX, TXT, MD, ODT, RTF, CSV, JSON, EPUB) and convert to Markdown."""
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == '.txt':
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        elif ext == '.md':
            # Markdown - already structured, convert to enhanced markdown
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                return _enhance_markdown(content)
        elif ext == '.pdf':
            text = ''
            with open(file_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + '\n'
            return _pdf_to_markdown(text.strip())
        elif ext == '.docx':
            doc = Document(file_path)
            return _docx_to_markdown(doc)
        elif ext == '.odt':
            return _extract_odt(file_path)
        elif ext == '.rtf':
            return _extract_rtf(file_path)
        elif ext == '.csv':
            return _extract_csv(file_path)
        elif ext == '.json':
            return _extract_json(file_path)
        elif ext == '.epub':
            return _extract_epub(file_path)
        else:
            return None
    except Exception:
        return None


def scan_gguf_models(models_dir: str = '/models') -> Dict[str, Any]:
    """Scan all GGUF files in directory and extract metadata.

    Args:
        models_dir: Directory containing GGUF model files

    Returns:
        Dict mapping model_name -> metadata dict
    """
    import glob

    result = {}

    try:
        from gguf import GGUFReader
    except ImportError:
        return result

    if not os.path.exists(models_dir):
        return result

    gguf_patterns = [
        os.path.join(models_dir, '*.gguf'),
        os.path.join(models_dir, '*', '*.gguf'),
    ]

    gguf_files = set()
    for pattern in gguf_patterns:
        gguf_files.update(glob.glob(pattern, recursive=True))

    for gguf_path in sorted(gguf_files):
        try:
            model_name = os.path.basename(gguf_path)
            if model_name.endswith('.gguf'):
                model_name = model_name[:-5]

            reader = GGUFReader(gguf_path)
            fields = reader.fields

            info = {'context_length': None, 'embedding_length': None, 'architecture': None, 'block_count': None, 'file_size_mb': None}

            for key in fields.keys():
                if key.endswith('.context_length') and info['context_length'] is None:
                    val = fields[key].parts[-1]
                    if hasattr(val, 'tolist'):
                        arr = val.tolist()
                        if isinstance(arr, list) and len(arr) == 1:
                            val = arr[0]
                    if val is not None:
                        info['context_length'] = int(val)
                        break

            for key in fields.keys():
                if key.endswith('.block_count') and info['block_count'] is None:
                    val = fields[key].parts[-1]
                    if hasattr(val, 'tolist'):
                        arr = val.tolist()
                        if isinstance(arr, list) and len(arr) == 1:
                            val = arr[0]
                    if val is not None:
                        info['block_count'] = int(val)
                        break

            for key in fields.keys():
                if key.endswith('.embedding_length') and info['embedding_length'] is None:
                    val = fields[key].parts[-1]
                    if hasattr(val, 'tolist'):
                        arr = val.tolist()
                        if isinstance(arr, list) and len(arr) == 1:
                            val = arr[0]
                    if val is not None:
                        info['embedding_length'] = int(val)
                        break

            if 'general.architecture' in fields:
                val = fields['general.architecture'].parts[-1]
                if hasattr(val, 'tolist'):
                    info['architecture'] = bytes(val.tolist()).decode('utf-8', errors='replace')
                else:
                    info['architecture'] = str(val)

            if 'general.size_label' in fields:
                val = fields['general.size_label'].parts[-1]
                if hasattr(val, 'tolist'):
                    info['size_label'] = bytes(val.tolist()).decode('utf-8', errors='replace')
                else:
                    info['size_label'] = str(val)

            if gguf_path and os.path.exists(gguf_path):
                info['file_size_mb'] = os.path.getsize(gguf_path) / (1024 * 1024)

            if info['context_length'] or info['embedding_length'] or info['architecture']:
                result[model_name] = info

        except Exception:
            continue

    return result


_gguf_models_cache = None


def get_gguf_models_cached(models_dir: str = '/models') -> Dict[str, Any]:
    """Get cached GGUF models metadata (scanned once at startup).

    Args:
        models_dir: Directory containing GGUF model files

    Returns:
        Dict mapping model_name -> metadata dict
    """
    global _gguf_models_cache
    if _gguf_models_cache is None:
        _gguf_models_cache = scan_gguf_models(models_dir)
    return _gguf_models_cache


def get_gguf_model_info(model_path: str) -> Dict[str, Any]:
    """Read metadata from GGUF model file.

    Args:
        model_path: Full path to GGUF file

    Returns:
        Dict with keys: context_length, embedding_length, architecture, params, quantization
    """
    result = {
        'context_length': None,
        'embedding_length': None,
        'architecture': None,
        'block_count': None,
        'file_size_mb': None,
    }

    try:
        from gguf import GGUFReader
    except ImportError:
        result['error'] = 'gguf library not installed'
        return result

    try:
        reader = GGUFReader(model_path)
        fields = reader.fields

        for key in fields.keys():
            if key.endswith('.context_length') and result['context_length'] is None:
                val = fields[key].parts[-1]
                if hasattr(val, 'tolist'):
                    arr = val.tolist()
                    if isinstance(arr, list) and len(arr) == 1:
                        val = arr[0]
                if val is not None:
                    result['context_length'] = int(val)
                    break

        for key in fields.keys():
            if key.endswith('.block_count') and result['block_count'] is None:
                val = fields[key].parts[-1]
                if hasattr(val, 'tolist'):
                    arr = val.tolist()
                    if isinstance(arr, list) and len(arr) == 1:
                        val = arr[0]
                if val is not None:
                    result['block_count'] = int(val)
                    break

        if 'general.architecture' in fields:
            val = fields['general.architecture'].parts[-1]
            if hasattr(val, 'tolist'):
                result['architecture'] = bytes(val.tolist()).decode('utf-8', errors='replace')
            else:
                result['architecture'] = str(val)

        if 'general.size_label' in fields:
            val = fields['general.size_label'].parts[-1]
            if hasattr(val, 'tolist'):
                result['size_label'] = bytes(val.tolist()).decode('utf-8', errors='replace')
            else:
                result['size_label'] = str(val)

        if model_path and os.path.exists(model_path):
            result['file_size_mb'] = os.path.getsize(model_path) / (1024 * 1024)

    except Exception as e:
        result['error'] = str(e)

    return result


def find_gguf_file(model_name: str, models_dir: str = '/models') -> Optional[str]:
    """Find GGUF file path for a given model name."""
    import glob

    model_basename = os.path.basename(model_name)
    if not model_basename.endswith('.gguf'):
        model_basename += '.gguf'

    patterns = [
        os.path.join(models_dir, model_basename),
        os.path.join(models_dir, model_name, '*.gguf'),
        os.path.join(models_dir, model_name.replace(' ', '_'), '*.gguf'),
        os.path.join(models_dir, '**', model_basename),
    ]

    for pattern in patterns:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return matches[0]

    return None


def _pdf_to_markdown(text: str) -> str:
    """Convert PDF extracted text to Markdown with structure."""
    lines = text.split('\n')
    md_lines = []
    current_heading = ""
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Detect year patterns (job periods) - convert to heading
        if re.search(r'(19|20)\d{2}', line) and ('–' in line or '-' in line):
            md_lines.append(f"\n## {line}\n")
        # Detect company names (capitalized)
        elif line and line[0].isupper() and len(line) > 10:
            md_lines.append(f"### {line}")
        else:
            md_lines.append(line)
    
    return '\n'.join(md_lines)


def _docx_to_markdown(doc: Document) -> str:
    """Convert DOCX paragraphs to Markdown."""
    md_parts = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        
        # Check for heading style
        if para.style.name.startswith('Heading'):
            level = para.style.name.replace('Heading ', '')
            try:
                level = int(level)
                md_parts.append(f"{'#' * min(level, 6)} {text}")
            except:
                md_parts.append(f"## {text}")
        else:
            md_parts.append(text)
    
    return '\n'.join(md_parts)


def _extract_odt(file_path: str) -> str:
    """Extract text from ODT and convert to Markdown."""
    try:
        from odf.opendocument import load
        from odf.text import P
        
        doc = load(file_path)
        paragraphs = doc.getElementsByType(P)
        
        md_lines = []
        for para in paragraphs:
            text = ''.join([str(c) for c in para.childNodes]).strip()
            if text:
                md_lines.append(text)
        
        return _pdf_to_markdown('\n'.join(md_lines))
    except ImportError:
        current_app.logger.warning("odfpy not installed, using plain text for ODT")
        return _extract_plain_text(file_path)


def _extract_rtf(file_path: str) -> str:
    """Extract text from RTF and convert to Markdown."""
    try:
        import striprtf
        with open(file_path, 'rb') as f:
            content = f.read()
        text = striprtf.parse_rtf(content)
        return _pdf_to_markdown(text)
    except ImportError:
        current_app.logger.warning("striprtf not installed, using plain text for RTF")
        return _extract_plain_text(file_path)


def _extract_csv(file_path: str) -> str:
    """Convert CSV to Markdown table."""
    import csv
    md_lines = ["# Data\n"]
    
    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        headers = next(reader, [])
        
        if headers:
            md_lines.append("| " + " | ".join(headers) + " |")
            md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
            
            for row in reader:
                md_lines.append("| " + " | ".join(row) + " |")
    
    return '\n'.join(md_lines)


def _extract_json(file_path: str) -> str:
    """Convert JSON to Markdown with structure."""
    import json
    
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    md_lines = ["# JSON Data\n"]
    
    if isinstance(data, dict):
        for key, value in data.items():
            md_lines.append(f"\n## {key}\n")
            md_lines.append(_json_value_to_markdown(value))
    elif isinstance(data, list):
        md_lines.append("\n## Items\n")
        for i, item in enumerate(data):
            md_lines.append(f"\n### Item {i+1}\n")
            md_lines.append(_json_value_to_markdown(item))
    
    return '\n'.join(md_lines)


def _json_value_to_markdown(value, indent=0) -> str:
    """Recursively convert JSON value to Markdown."""
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            lines.append(f"**{k}**: {_json_value_to_markdown(v, indent+1)}")
        return '\n'.join(lines)
    elif isinstance(value, list):
        return '\n'.join([f"- {item}" for item in value])
    else:
        return str(value)


def _extract_epub(file_path: str) -> str:
    """Extract text from EPUB and convert to Markdown."""
    try:
        import epub
        md_lines = ["# Book\n"]
        
        book = epub.read_epub(file_path)
        
        for item in book.get_items():
            if item.get_type() == 9:  # Epub HTML
                content = item.get_content()
                # Simple HTML to text conversion
                text = re.sub(r'<[^>]+>', '', content)
                text = text.strip()
                if text:
                    md_lines.append(text)
        
        return _pdf_to_markdown('\n'.join(md_lines))
    except ImportError:
        current_app.logger.warning("epub not installed, using plain text for EPUB")
        return _extract_plain_text(file_path)


def _extract_plain_text(file_path: str) -> str:
    """Fallback: extract plain text from file."""
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()


def _enhance_markdown(content: str) -> str:
    """Enhance existing Markdown with structure."""
    lines = content.split('\n')
    enhanced = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Detect headers
        if line.isupper() and len(line) < 100:
            enhanced.append(f"## {line}")
        # Detect year patterns
        elif re.search(r'(19|20)\d{2}', line):
            enhanced.append(f"\n### {line}\n")
        else:
            enhanced.append(line)
    
    return '\n'.join(enhanced)


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


def chunk_text_recursive(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50,
    separators: List[str] = None
) -> List[str]:
    """Split text using recursive splitting strategy.
    
    Args:
        text: Input text to split
        chunk_size: Maximum chunk size in characters
        overlap: Overlap between chunks in characters
        separators: List of separators to try in order of priority
                   (default: paragraphs, sentences, words)
    
    Returns:
        List of text chunks
    """
    if separators is None:
        separators = [
            '\n\n',    # Double newline = paragraph break
            '\n',      # Single newline = line break
            '. ',      # Sentence boundary
            '; ',      # Semicolon clause
            ', ',      # Comma clause
            ' ',       # Word boundary (fallback)
        ]
    
    def split_by_separator(text: str, sep: str) -> List[str]:
        if sep == ' ':
            return text.split(sep)
        parts = text.split(sep)
        # Re-add separator to all parts except last
        return [parts[i] + sep if i < len(parts) - 1 else parts[i] for i in range(len(parts)) if parts[i]]
    
    def recursive_split(text: str, sep_index: int) -> List[str]:
        """Recursively split text until chunks are small enough."""
        if sep_index >= len(separators):
            # Final fallback: split by words
            words = text.split()
            chunks = []
            i = 0
            while i < len(words):
                chunk = ' '.join(words[i:i + chunk_size])
                if chunk:
                    chunks.append(chunk)
                i += max(1, chunk_size - overlap)
            return chunks
        
        parts = split_by_separator(text, separators[sep_index])
        
        # If splitting produced too few parts, try next separator
        if len(parts) <= 1:
            return recursive_split(text, sep_index + 1)
        
        # If parts are small enough, use them
        small_enough = [p for p in parts if len(p) <= chunk_size]
        if len(small_enough) == len(parts):
            return parts
        
        # Otherwise, recursively split large parts
        chunks = []
        for part in parts:
            if len(part) <= chunk_size:
                chunks.append(part)
            else:
                chunks.extend(recursive_split(part, sep_index + 1))
        
        return chunks
    
    # Normalize text: normalize whitespace
    text = ' '.join(text.split())
    
    # Handle empty or very short text
    if not text or len(text) <= chunk_size:
        return [text] if text else []
    
    chunks = recursive_split(text, 0)
    
    # Final pass: merge small chunks with neighbors and apply overlap
    merged = []
    for i, chunk in enumerate(chunks):
        # Skip duplicates
        if merged and merged[-1] == chunk:
            continue
        
        # Merge with previous if both are small
        if merged and len(merged[-1]) + len(chunk) <= chunk_size:
            merged[-1] = merged[-1] + ' ' + chunk
        else:
            merged.append(chunk)
    
    return merged


def chunk_text_by_sentences(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50,
    min_sentences: int = 1
) -> List[str]:
    """Split text into chunks by sentences, with optional size limit.
    
    Args:
        text: Input text
        chunk_size: Max characters per chunk
        overlap: Character overlap between chunks
        min_sentences: Minimum sentences per chunk
    
    Returns:
        List of text chunks
    """
    # Simple sentence splitting (works for Russian and English)
    import re
    sentences = re.split(r'(?<=[.!?])\s+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    
    if not sentences:
        return []
    
    chunks = []
    current_chunk = ""
    
    for sentence in sentences:
        # If adding this sentence exceeds limit, save current and start new
        if current_chunk and len(current_chunk) + len(sentence) > chunk_size:
            chunks.append(current_chunk)
            # Keep overlap (last part of current chunk)
            if overlap > 0 and len(current_chunk) > overlap:
                current_chunk = current_chunk[-(overlap):] + " " + sentence
            else:
                current_chunk = sentence
        else:
            current_chunk = (current_chunk + " " + sentence).strip() if current_chunk else sentence
    
    # Don't forget last chunk
    if current_chunk:
        chunks.append(current_chunk)
    
    return chunks


def estimate_tokens(text: str, model_type: str = 'chat', lang: str = 'ru', token_chars: float = None) -> int:
    """
    Estimate tokens based on characters per token with language and model-specific coefficients.
    
    Args:
        text: Input text to estimate
        model_type: Type of model ('chat', 'reasoning', 'multimodal', 'embedding')
        lang: Language code ('ru', 'en')
        token_chars: Override coefficient (if None, uses predefined coefficients)
    
    Returns:
        Estimated token count
    """
    if not text:
        return 0
    
    # Use provided coefficient or get from predefined table
    if token_chars is not None:
        coeff = token_chars
    else:
        coeff = TOKEN_COEFFICIENTS.get((model_type, lang), 3.0)
    
    # Apply safety margin to estimation
    estimated = len(text) / coeff + 1
    return int(estimated * SAFETY_MARGIN)


def build_context_prompt(history: List[Dict[str, str]], lang: str = 'ru') -> str:
    """Format conversation history into a string."""
    if not history:
        return ""
    lines = []
    for msg in history:
        role = "User" if msg['role'] == 'user' else "Assistant"
        lines.append(f"{role}: {msg['content']}")
    return "\n".join(lines)


def validate_prompt_size(prompt: str, model_config: Dict[str, Any], model_type: str = 'chat', lang: str = 'ru') -> Tuple[bool, int, int]:
    """
    Validate that prompt fits within model's context window with safety margin.

    Returns:
        (is_valid, estimated_tokens, max_tokens)
    """
    if not model_config:
        return True, 0, 0

    max_context = model_config.get('context_length', 32768)
    estimated = estimate_tokens(prompt, model_type, lang)

    # Use 95% of context as hard limit
    hard_limit = int(max_context * 0.95)

    is_valid = estimated <= hard_limit
    return is_valid, estimated, max_context


def validate_session_ownership(session_id: str, user_id: str) -> bool:
    """
    Validate that a session belongs to the given user.
    
    Args:
        session_id: UUID of the session
        user_id: User login to validate against
    
    Returns:
        True if session exists and belongs to user, False otherwise
    """
    import uuid
    from .database import get_db

    # Validate UUID format first
    try:
        uuid.UUID(session_id, version=4)
    except (ValueError, AttributeError):
        return False

    # Check ownership
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT user_id FROM chat_sessions WHERE id = %s', (session_id,))
            row = c.fetchone()
            return row is not None and row['user_id'] == user_id
    except Exception:
        return False


def check_upload_quota(user_id: str, additional_bytes: int) -> Optional[str]:
    """Check if user has exceeded upload storage quota. O(1) lookup.

    Args:
        user_id: User login
        additional_bytes: Size of the new upload in bytes

    Returns:
        Error message if quota exceeded, None if OK
    """
    from . import db

    max_mb = current_app.config.get('MAX_UPLOAD_STORAGE_MB', 500)
    max_bytes = max_mb * 1024 * 1024

    # O(1): read from SQLite counter instead of walking directory tree
    total_used = db.get_user_storage_usage(user_id)

    if total_used + additional_bytes > max_bytes:
        used_mb = total_used / (1024 * 1024)
        return f"Storage quota exceeded: {used_mb:.0f}MB / {max_mb}MB used. Delete some files to free space."
    return None


def check_document_quota(user_id: str) -> Optional[str]:
    """Check if user has exceeded document quota.

    Args:
        user_id: User login

    Returns:
        Error message if quota exceeded, None if OK
    """
    from .database import get_db

    max_docs = current_app.config.get('MAX_DOCUMENTS_PER_USER', 50)
    max_mb = current_app.config.get('MAX_DOCUMENTS_STORAGE_MB', 50)

    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute('SELECT COUNT(*), COALESCE(SUM(file_size), 0) FROM documents WHERE user_id = %s', (user_id,))
            row = c.fetchone()
            count, total_bytes = row['count'], row['coalesce']

            if count >= max_docs:
                return f"Document quota exceeded: {count} / {max_docs} documents. Delete some to upload more."
            if total_bytes + 1 > max_mb * 1024 * 1024:
                used_mb = total_bytes / (1024 * 1024)
                return f"Document storage quota exceeded: {used_mb:.0f}MB / {max_mb}MB used."
    except Exception:
        pass  # Don't block upload on DB errors

    return None