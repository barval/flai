from flask import current_app
import pytz
from datetime import datetime
import os

def get_current_time_in_timezone():
    """Возвращает текущее время в часовом поясе, указанном в .env, в читаемом формате."""
    tz = current_app.config.get('TIMEZONE')
    if not tz:
        current_app.logger.error("Часовой пояс не настроен")
        return None
    try:
        utc_now = datetime.now(pytz.UTC)
        local_time = utc_now.astimezone(tz)
        weekdays_ru = {
            0: 'понедельник', 1: 'вторник', 2: 'среда',
            3: 'четверг', 4: 'пятница', 5: 'суббота', 6: 'воскресенье'
        }
        formatted_date = local_time.strftime('%d.%m.%Y')
        formatted_time = local_time.strftime('%H:%M:%S')
        weekday_ru = weekdays_ru[local_time.weekday()]
        tz_abbr = local_time.strftime('%z')
        if tz_abbr:
            tz_abbr = f"(+{int(tz_abbr[1:3])})" if tz_abbr.startswith('+') else f"({tz_abbr})"
        else:
            tz_abbr = ""
        return f"{formatted_date} {formatted_time} {weekday_ru} {tz_abbr}"
    except Exception as e:
        current_app.logger.error(f"Ошибка получения времени: {str(e)}")
        return None

def get_current_time_in_timezone_for_db():
    """Возвращает текущее время в формате SQLite."""
    tz = current_app.config.get('TIMEZONE')
    if not tz:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        utc_now = datetime.now(pytz.UTC)
        local_time = utc_now.astimezone(tz)
        return local_time.strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        current_app.logger.error(f"Ошибка получения времени: {str(e)}")
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

PROMPTS_DIR = 'prompts'

def load_prompt_template(template_name):
    """Загружает шаблон промпта из файла."""
    template_path = os.path.join(PROMPTS_DIR, template_name)
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        current_app.logger.error(f"Шаблон не найден: {template_path}")
        return None
    except Exception as e:
        current_app.logger.error(f"Ошибка загрузки шаблона {template_name}: {str(e)}")
        return None

def format_prompt(template_name, variables):
    """Загружает шаблон и подставляет переменные."""
    template = load_prompt_template(template_name)
    if not template:
        return None
    try:
        return template.format(**variables)
    except KeyError as e:
        current_app.logger.error(f"Отсутствует переменная в шаблоне {template_name}: {e}")
        return None
    except Exception as e:
        current_app.logger.error(f"Ошибка форматирования шаблона {template_name}: {str(e)}")
        return None