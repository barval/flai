from flask import current_app
import pytz
from datetime import datetime
import os

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