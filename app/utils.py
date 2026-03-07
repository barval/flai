from flask import current_app
import pytz
from datetime import datetime
import os
import base64
from io import BytesIO
from PIL import Image

def get_current_time_in_timezone(app=None):
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
        weekdays_en = {
            0: 'Monday', 1: 'Tuesday', 2: 'Wednesday',
            3: 'Thursday', 4: 'Friday', 5: 'Saturday', 6: 'Sunday'
        }
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

def get_current_time_in_timezone_for_db(app=None):
    """Returns current time in SQLite format (YYYY-MM-DD HH:MM:SS) using configured timezone."""
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

PROMPTS_DIR = 'prompts'

def load_prompt_template(template_name, lang='ru'):
    """Load prompt template from language-specific subfolder."""
    template_path = os.path.join(PROMPTS_DIR, lang, template_name)
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        if lang != 'ru':
            # Fallback to Russian
            return load_prompt_template(template_name, 'ru')
        current_app.logger.error(f"Template not found: {template_path}")
        return None
    except Exception as e:
        current_app.logger.error(f"Error loading template {template_name}: {str(e)}")
        return None

def format_prompt(template_name, variables, lang='ru'):
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

def resize_image_if_needed(file_data, file_type, file_name, max_width, max_height, quality=85):
    """
    Resize image if it exceeds max dimensions.
    Returns tuple (new_file_data, new_file_type, new_file_name, resized_flag, original_dimensions, new_dimensions)
    - new_file_data: base64 encoded image data (JPEG)
    - new_file_type: always 'image/jpeg'
    - new_file_name: filename with .jpg extension
    - resized_flag: True if resize was performed
    - original_dimensions: (width, height) or None
    - new_dimensions: (width, height) or None
    """
    try:
        image_bytes = base64.b64decode(file_data)
        img = Image.open(BytesIO(image_bytes))
        original_width, original_height = img.size

        # Check if resize needed
        if original_width <= max_width and original_height <= max_height:
            return file_data, file_type, file_name, False, None, None

        # Calculate new dimensions preserving aspect ratio
        ratio = min(max_width / original_width, max_height / original_height)
        new_width = int(original_width * ratio)
        new_height = int(original_height * ratio)

        # Resize with high-quality downsampling
        img_resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        # Convert to RGB (in case of RGBA, remove alpha channel)
        if img_resized.mode in ('RGBA', 'LA', 'P'):
            rgb_img = Image.new('RGB', img_resized.size, (255, 255, 255))
            rgb_img.paste(img_resized, mask=img_resized.split()[-1] if img_resized.mode == 'RGBA' else None)
            img_resized = rgb_img

        # Save as JPEG to BytesIO
        output = BytesIO()
        img_resized.save(output, format='JPEG', quality=quality, optimize=True)
        output_bytes = output.getvalue()
        new_file_data = base64.b64encode(output_bytes).decode('utf-8')
        new_file_type = 'image/jpeg'

        # Change file extension to .jpg
        base, _ = os.path.splitext(file_name)
        new_file_name = base + '.jpg'

        return new_file_data, new_file_type, new_file_name, True, (original_width, original_height), (new_width, new_height)

    except Exception as e:
        current_app.logger.error(f"Error resizing image: {str(e)}")
        # Return original data on error
        return file_data, file_type, file_name, False, None, None