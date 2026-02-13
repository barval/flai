from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory
import os
import sqlite3
import json
import base64
from datetime import datetime
from dotenv import load_dotenv
import mimetypes
import uuid
import requests
import time
from PIL import Image
from io import BytesIO
import pytz
from pytz.exceptions import UnknownTimeZoneError

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key')
app.config['JSON_AS_ASCII'] = False
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

# -------------------------------
# Подпись в футере - единая для всего проекта
# -------------------------------
FOOTER_TEXT = os.getenv('FOOTER_TEXT', 'ИИ Локальный v1.2 (с) 2026 Барсуков Валерий')

# -------------------------------
# Настройки часового пояса из .env
# -------------------------------
TIMEZONE_STR = os.getenv('TIMEZONE', 'Europe/Moscow')  # По умолчанию Москва

# Проверяем валидность часового пояса
try:
    TIMEZONE = pytz.timezone(TIMEZONE_STR)
    app.logger.info(f"Используется часовой пояс: {TIMEZONE_STR}")
except UnknownTimeZoneError:
    app.logger.warning(f"Неизвестный часовой пояс '{TIMEZONE_STR}'. Используется UTC.")
    TIMEZONE = pytz.UTC
    TIMEZONE_STR = 'UTC'

# -------------------------------
# Настройки Ollama
# -------------------------------
OLLAMA_URL = os.getenv('OLLAMA_URL', 'http://localhost:11434')
OLLAMA_CHAT_MODEL = os.getenv('LLM_CHAT_MODEL', 'qwen3-vl:8b-instruct-q4_K_M')
OLLAMA_MULTIMODAL_MODEL = os.getenv('LLM_MULTIMODAL_MODEL', 'qwen3-vl:8b-instruct-q4_K_M')
OLLAMA_REASONING_MODEL = os.getenv('LLM_REASONING_MODEL', 'gpt-oss-20b')

# Контекстные окна для разных моделей
MODEL_CONTEXT_WINDOWS = {
    OLLAMA_CHAT_MODEL: int(os.getenv('LLM_CHAT_MODEL_CONTEXT_WINDOW', 32768)),
    OLLAMA_MULTIMODAL_MODEL: int(os.getenv('LLM_MULTIMODAL_MODEL_CONTEXT_WINDOW', 32768)),
    OLLAMA_REASONING_MODEL: int(os.getenv('LLM_REASONING_MODEL_CONTEXT_WINDOW', 65536)),
}

# -------------------------------
# Поддерживаемые форматы изображений
# -------------------------------
SUPPORTED_IMAGE_EXTENSIONS = {
    '.jpg', '.jpeg', '.jpe',  # JPEG
    '.png',                     # PNG
    '.bmp',                     # BMP
    '.webp',                    # WebP
    '.tif', '.tiff'             # TIFF
}

SUPPORTED_IMAGE_MIMETYPES = {
    'image/jpeg', 'image/jpg', 'image/jpe',
    'image/png',
    'image/bmp', 'image/x-ms-bmp',
    'image/webp',
    'image/tiff', 'image/tif'
}

# Ограничения для изображений
MAX_IMAGE_SIZE_MB = 5
MAX_IMAGE_SIZE_BYTES = MAX_IMAGE_SIZE_MB * 1024 * 1024
MAX_IMAGE_DIMENSION = 3840  # 3840×2160

# -------------------------------
# Настройка путей к БД
# -------------------------------
DATA_DIR = 'data'
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

CHAT_DB_PATH = os.path.join(DATA_DIR, 'chat.db')

# -------------------------------
# Функция для получения текущего времени в заданном часовом поясе
# -------------------------------
def get_current_time_in_timezone():
    """
    Возвращает текущее время в часовом поясе, указанном в .env
    Формат: ДД.ММ.ГГГГ день_недели ЧЧ:ММ:СС
    """
    try:
        # Получаем текущее время в UTC
        utc_now = datetime.now(pytz.UTC)
        
        # Конвертируем в нужный часовой пояс
        local_time = utc_now.astimezone(TIMEZONE)
        
        # Дни недели на русском
        weekdays_ru = {
            0: 'понедельник',
            1: 'вторник', 
            2: 'среда',
            3: 'четверг',
            4: 'пятница',
            5: 'суббота',
            6: 'воскресенье'
        }
        
        # Форматируем дату и время
        formatted_date = local_time.strftime('%d.%m.%Y')
        formatted_time = local_time.strftime('%H:%M:%S')
        weekday_ru = weekdays_ru[local_time.weekday()]
        
        # Добавляем часовой пояс для информации
        tz_abbr = local_time.strftime('%z')
        if tz_abbr:
            tz_abbr = f" ({tz_abbr})"
        else:
            tz_abbr = ""
        
        return f"{formatted_date} {weekday_ru} {formatted_time}{tz_abbr}"
    
    except Exception as e:
        app.logger.error(f"Ошибка получения времени в часовом поясе {TIMEZONE_STR}: {str(e)}")
        # Fallback на UTC с днем недели на английском
        utc_time = datetime.now(pytz.UTC)
        weekdays_en = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        return f"{utc_time.strftime('%d.%m.%Y')} {weekdays_en[utc_time.weekday()]} {utc_time.strftime('%H:%M:%S')} UTC"

# -------------------------------
# Функция для получения информации о текущем часовом поясе
# -------------------------------
def get_timezone_info():
    """Возвращает информацию о текущем часовом поясе для отладки"""
    return {
        'timezone': TIMEZONE_STR,
        'utc_offset': datetime.now(TIMEZONE).strftime('%z'),
        'current_time': get_current_time_in_timezone()
    }

# -------------------------------
# Функция для проверки изображения
# -------------------------------
def validate_image_file(file_data, file_type, file_name, file_size):
    """
    Проверяет, является ли файл поддерживаемым изображением и соответствует ли ограничениям
    Возвращает (is_valid, error_message)
    """
    # Проверка размера файла
    if file_size > MAX_IMAGE_SIZE_BYTES:
        return False, f"Максимальный размер файла с изображением {MAX_IMAGE_SIZE_MB}Мб"
    
    # Проверка MIME-типа
    if file_type not in SUPPORTED_IMAGE_MIMETYPES:
        # Проверяем по расширению, если MIME-тип неопределенный
        ext = os.path.splitext(file_name)[1].lower()
        if ext not in SUPPORTED_IMAGE_EXTENSIONS:
            return False, "Файлы данного типа пока не поддерживаются."
    
    # Проверка размеров изображения
    try:
        # Декодируем base64 в байты
        image_bytes = base64.b64decode(file_data)
        
        # Открываем изображение через PIL
        img = Image.open(BytesIO(image_bytes))
        width, height = img.size
        
        # Проверяем максимальное разрешение
        if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
            return False, f"Максимальное разрешение файла с изображением - не более {MAX_IMAGE_DIMENSION}×{MAX_IMAGE_DIMENSION}"
        
        return True, None
        
    except Exception as e:
        app.logger.error(f"Ошибка при проверке изображения: {str(e)}")
        return False, "Не удалось обработать файл изображения"

# -------------------------------
# Функции для работы с Ollama
# -------------------------------
def check_ollama_connection():
    """Проверка подключения к Ollama"""
    try:
        response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if response.status_code == 200:
            models = response.json().get('models', [])
            app.logger.info(f"Ollama connected. Available models: {[m['name'] for m in models]}")
            return True, models
        else:
            return False, []
    except Exception as e:
        app.logger.error(f"Ollama connection failed: {str(e)}")
        return False, []

def call_ollama_chat(messages, model=None, stream=False):
    """Вызов Ollama API для чата"""
    if model is None:
        model = OLLAMA_CHAT_MODEL
    
    try:
        payload = {
            'model': model,
            'messages': messages,
            'stream': stream,
            'options': {
                'num_ctx': MODEL_CONTEXT_WINDOWS.get(model, 32768),
                'temperature': 0.7,
                'top_p': 0.9,
            }
        }
        
        app.logger.info(f"Отправка запроса к Ollama. Модель: {model}")
        if any('images' in msg for msg in messages):
            app.logger.info("Запрос содержит изображение(я)")
        
        response = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json=payload,
            timeout=120
        )
        
        if response.status_code == 200:
            result = response.json()
            app.logger.info("Успешный ответ от Ollama")
            return result['message']['content']
        else:
            error_msg = f"Ollama error: {response.status_code} - {response.text}"
            app.logger.error(error_msg)
            return f"⚠️ Ошибка Ollama: {response.status_code}"
            
    except requests.exceptions.ConnectionError:
        app.logger.error("Ошибка подключения к Ollama")
        return "⚠️ Не удалось подключиться к Ollama. Проверьте, запущен ли сервис."
    except requests.exceptions.Timeout:
        app.logger.error("Таймаут при обращении к Ollama")
        return "⚠️ Превышено время ожидания ответа от Ollama. Попробуйте ещё раз."
    except Exception as e:
        app.logger.error(f"Error calling Ollama: {str(e)}")
        return f"⚠️ Ошибка при обращении к Ollama: {str(e)}"

# -------------------------------
# Ключевые слова для разных типов задач
# -------------------------------

# 1. ЛИТЕРАТУРА (рассказы, сочинения, пересказы, тексты)
LITERATURE_KEYWORDS = [
    # Русские - общие
    'рассказ', 'рассказы', 'напиши рассказ', 'сочини рассказ',
    'сочинение', 'напиши сочинение', 'эссе', 'напиши эссе',
    'пересказ', 'перескажи', 'краткое содержание', 'изложение',
    'текст', 'напиши текст', 'создай текст', 'составь текст',
    'описание', 'опиши', 'кратко опиши',
    'повествование', 'повествуй', 'оповести',
    'история', 'напиши историю', 'придумай историю',
    'сказка', 'напиши сказку', 'сочини сказку',
    'стих', 'стихи', 'напиши стих', 'сочини стих', 'поэма',
    'басня', 'напиши басню', 'притча', 'напиши притчу',
    'легенда', 'миф', 'былина', 'сказание',
    'очерк', 'напиши очерк', 'заметка', 'статья',
    'письмо', 'напиши письмо', 'послание',
    'дневник', 'запись в дневнике', 'воспоминания', 'мемуары',
    'биография', 'автобиография', 'жизнеописание',
    'рецензия', 'напиши рецензию', 'отзыв', 'напиши отзыв',
    'аннотация', 'напиши аннотацию', 'краткое описание',
    
    # Русские - школьная программа
    'сочинение по литературе', 'анализ стихотворения',
    'образ главного героя', 'характеристика персонажа',
    'тема произведения', 'идея произведения', 'основная мысль',
    'сюжет', 'композиция', 'кульминация', 'развязка',
    'литературный герой', 'литературный персонаж',
    'литературное направление', 'романтизм', 'реализм', 'классицизм',
    'эпитет', 'метафора', 'сравнение', 'олицетворение', 'гипербола',
    'рифма', 'ритм', 'размер стиха', 'строфа',
    
    # Русские - жанры
    'роман', 'повесть', 'новелла', 'пьеса', 'драма', 'комедия', 'трагедия',
    'фэнтези', 'фантастика', 'детектив', 'приключения', 'любовный роман',
    'триллер', 'мистика', 'ужасы', 'хоррор', 'постапокалипсис',
    'антиутопия', 'утопия', 'альтернативная история',
    
    # Русские - действия
    'придумай', 'выдумай', 'сочини', 'напиши', 'создай', 'составь',
    'перескажи', 'изложи', 'опиши', 'расскажи', 'поведай',
    'проанализируй текст', 'разбери текст', 'прокомментируй',
    
    # Русские - фразы
    'напиши небольшой рассказ', 'напиши короткий рассказ',
    'напиши интересную историю', 'придумай увлекательную историю',
    'сочини стихотворение', 'сочини стих на тему',
    'сделай пересказ', 'сделай краткий пересказ',
    'напиши сочинение на тему', 'помоги написать сочинение',
    'как написать рассказ', 'как написать сочинение',
    'что написать в сочинении', 'о чем написать в рассказе',
    
    # Английские термины
    'story', 'stories', 'write a story', 'tell a story',
    'essay', 'write an essay', 'composition',
    'retelling', 'retell', 'summary', 'summarize',
    'text', 'write text', 'create text',
    'description', 'describe', 'briefly describe',
    'narrative', 'narrate', 'narration',
    'history', 'write history', 'create history',
    'fairy tale', 'write a fairy tale', 'fable',
    'poem', 'poetry', 'write a poem', 'verse',
    'parable', 'legend', 'myth', 'epic',
    'sketch', 'article', 'letter', 'write a letter',
    'diary', 'memoir', 'biography', 'autobiography',
    'review', 'write a review', 'feedback', 'annotation',
    
    # Английские - литературные термины
    'plot', 'character', 'protagonist', 'antagonist', 'hero',
    'theme', 'idea', 'message', 'symbolism', 'metaphor',
    'simile', 'personification', 'hyperbole', 'irony',
    'rhyme', 'rhythm', 'meter', 'stanza', 'verse',
    'genre', 'novel', 'novella', 'short story', 'play',
    'drama', 'comedy', 'tragedy', 'fantasy', 'science fiction',
    'mystery', 'detective', 'adventure', 'romance', 'thriller',
    'horror', 'dystopia', 'utopia', 'alternative history',
    
    # Английские - действия
    'create', 'invent', 'compose', 'write', 'make up',
    'retell', 'summarize', 'describe', 'tell', 'narrate',
    'analyze text', 'analyze the text', 'comment on',
    
    # Английские - фразы
    'write a short story', 'write an interesting story',
    'create a fascinating story', 'compose a poem',
    'give a summary', 'provide a summary',
    'help me write', 'how to write a story',
    'what to write about', 'ideas for a story'
]

# 2. ЛОГИКА (рассуждения, анализ, объяснения)
LOGIC_KEYWORDS = [
    # Русские
    'почему', 'зачем', 'объясни', 'объяснение', 'рассуждай', 'рассуждение',
    'думай', 'подумай', 'анализируй', 'анализ', 'проанализируй',
    'сравни', 'сравнение', 'спрогнозируй', 'прогноз', 'предскажи',
    'выведи', 'вывод', 'логика', 'логический', 'логически',
    'продумай', 'обдумай', 'умозаключение', 'умозаключи',
    'аргументируй', 'аргумент', 'доказательство', 'докажи',
    'обоснуй', 'обоснование', 'гипотеза', 'предположение',
    'противопоставь', 'противопоставление', 'выяви', 'выявление',
    'закономерность', 'закономерности', 'взаимосвязь', 'взаимосвязи',
    'причина', 'следствие', 'причинно-следственный', 'вытекает',
    'следовательно', 'отсюда следует', 'из этого следует',
    'если...то', 'при условии', 'в таком случае',
    'противоречие', 'противоречит', 'парадокс',
    'синтез', 'синтезируй', 'обобщи', 'обобщение',
    'концепция', 'концептуально', 'теоретически',
    'метод', 'методология', 'методологически',
    'критерий', 'критерии', 'параметр', 'параметры',
    'классифицируй', 'классификация', 'систематизируй',
    'интерпретируй', 'интерпретация', 'трактовка',
    
    # Русские фразы
    'как ты думаешь', 'каково твое мнение', 'что ты думаешь о',
    'какой вывод', 'к какому выводу', 'что из этого следует',
    'как это объяснить', 'чем это можно объяснить',
    'в чем разница', 'чем отличаются', 'что общего',
    'какой вариант лучше', 'что предпочтительнее',
    'как ты рассуждал', 'объясни ход мыслей',
    'почему ты так решил', 'на чем основано',
    
    # Английские
    'why', 'explain', 'reasoning', 'reason', 'think', 'thought',
    'analyze', 'analysis', 'compare', 'comparison', 'predict',
    'forecast', 'conclude', 'conclusion', 'logic', 'logical',
    'deduce', 'deduction', 'infer', 'inference', 'argument',
    'justify', 'justification', 'hypothesis', 'assumption',
    'synthesize', 'synthesis', 'generalize', 'generalization',
    'concept', 'conceptual', 'theoretical', 'methodology',
    'criteria', 'parameter', 'classify', 'classification',
    'interpret', 'interpretation', 'implication', 'consequence'
]

# 3. МАТЕМАТИКА (расчеты, формулы, числа)
MATH_KEYWORDS = [
    # Общая математика
    'математика', 'математический', 'математически',
    'вычисли', 'вычисление', 'расчет', 'рассчитать',
    'посчитай', 'подсчет', 'подсчитай', 'сосчитай',
    'формула', 'формулы', 'уравнение', 'уравнения',
    'функция', 'функции', 'график', 'графики',
    'интеграл', 'производная', 'дифференциал',
    'сумма', 'разность', 'произведение', 'частное',
    'корень', 'степень', 'логарифм', 'логарифмический',
    'экспонента', 'экспоненциальный', 'показатель',
    'синус', 'косинус', 'тангенс', 'тригонометрия',
    'теорема', 'аксиома', 'лемма', 'доказательство',
    'задача', 'решение', 'решить', 'решается',
    'пример', 'примеры', 'упражнение', 'упражнения',
    
    # Числа и операции
    'число', 'числа', 'цифра', 'цифры', 'количество',
    'процент', 'проценты', 'дробь', 'дроби',
    'деление', 'умножение', 'сложение', 'вычитание',
    'плюс', 'минус', 'умножить', 'разделить',
    'квадрат', 'куб', 'квадратный', 'кубический',
    'модуль', 'факториал', 'бином', 'комбинаторика',
    'вероятность', 'вероятностный', 'статистика',
    'среднее', 'медиана', 'мода', 'дисперсия',
    'стандартное отклонение', 'корреляция',
    
    # Геометрия
    'геометрия', 'геометрический', 'фигура', 'фигуры',
    'треугольник', 'квадрат', 'прямоугольник', 'круг',
    'окружность', 'эллипс', 'многоугольник', 'ромб',
    'параллелепипед', 'куб', 'шар', 'сфера', 'цилиндр',
    'конус', 'пирамида', 'призма', 'многогранник',
    'угол', 'сторона', 'диагональ', 'радиус', 'диаметр',
    'площадь', 'объем', 'периметр', 'длина', 'ширина',
    'высота', 'глубина', 'расстояние', 'координаты',
    
    # Время и даты (кроме простых вопросов о текущем времени)
    'сколько времени займет', 'через сколько времени',
    'сколько дней прошло', 'сколько месяцев прошло',
    'сколько лет прошло', 'разница во времени', 'разница в датах',
    'какой будет день через', 'какая будет дата через',
    'расчет времени', 'расчет даты', 'временной промежуток',
    'интервал времени', 'продолжительность', 'длительность',
    'срок', 'период', 'цикл', 'хронология', 'последовательность',
    'расписание', 'график работы', 'календарь', 'календарный',
    'високосный', 'високосный год', 'сезон', 'квартал',
    'десятилетие', 'век', 'тысячелетие', 'эра', 'эпоха',
    
    # Английские термины
    'math', 'mathematics', 'mathematical', 'calculate', 'calculation',
    'compute', 'computation', 'count', 'formula', 'equation',
    'function', 'graph', 'integral', 'derivative', 'sum', 'difference',
    'product', 'quotient', 'root', 'power', 'exponent', 'logarithm',
    'sine', 'cosine', 'tangent', 'trigonometry', 'theorem',
    'problem', 'solution', 'solve', 'example', 'exercise',
    'number', 'digit', 'percentage', 'fraction', 'decimal',
    'add', 'subtract', 'multiply', 'divide', 'plus', 'minus',
    'square', 'cube', 'quadratic', 'linear', 'algebra',
    'probability', 'statistics', 'average', 'mean', 'median',
    'mode', 'variance', 'deviation', 'correlation',
    'geometry', 'geometric', 'triangle', 'rectangle', 'circle',
    'sphere', 'cylinder', 'cone', 'pyramid', 'angle', 'side',
    'radius', 'diameter', 'area', 'volume', 'perimeter',
    'length', 'width', 'height', 'depth', 'distance', 'coordinates',
    
    # Расчеты времени на английском
    'time calculation', 'date calculation', 'how many days',
    'how many hours', 'time difference', 'date difference',
    'duration', 'interval', 'period', 'timeline', 'schedule',
    'calendar', 'leap year', 'century', 'decade', 'millennium'
]

# 4. ПРОГРАММИРОВАНИЕ (код, разработка)
PROGRAMMING_KEYWORDS = [
    # Общее программирование
    'программирование', 'программировать', 'программный',
    'код', 'напиши код', 'написать код', 'кодить',
    'разработка', 'разработать', 'разработчик',
    'алгоритм', 'алгоритмический', 'алгоритмы',
    'скрипт', 'скрипты', 'напиши скрипт', 'bash скрипт',
    'программа', 'напиши программу', 'создай программу',
    'приложение', 'разработать приложение', 'создать приложение',
    'сайт', 'создать сайт', 'разработать сайт', 'веб-сайт',
    'функция', 'функции', 'метод', 'методы', 'класс', 'классы',
    'библиотека', 'библиотеки', 'фреймворк', 'фреймворки',
    'API', 'интерфейс', 'бэкенд', 'фронтенд', 'фулстек',
    'отладка', 'дебаг', 'отладить', 'исправить ошибку',
    'оптимизация', 'оптимизировать', 'рефакторинг', 'рефакторить',
    
    # Языки программирования
    'python', 'питон', 'пайтон', 'java', 'джава',
    'javascript', 'js', 'typescript', 'ts', 'php',
    'c++', 'си плюс плюс', 'c#', 'си шарп', 'c', 'си',
    'ruby', 'руби', 'go', 'golang', 'rust', 'раст',
    'swift', 'kotlin', 'scala', 'perl', 'html', 'css',
    'sql', 'mysql', 'postgresql', 'sqlite', 'mongodb',
    
    # Конкретные задачи
    'напиши функцию', 'напиши класс', 'напиши метод',
    'создай функцию', 'создай класс', 'создай метод',
    'реализуй алгоритм', 'реализовать алгоритм',
    'сортировка', 'поиск', 'рекурсия', 'итерация',
    'парсинг', 'парсить', 'обработка данных',
    'работа с файлами', 'чтение файла', 'запись в файл',
    'база данных', 'бд', 'запрос к бд', 'sql запрос',
    'регулярные выражения', 'regex', 'регексп',
    'асинхронность', 'асинхронный', 'async', 'await',
    'многопоточность', 'многопроцессорность', 'thread',
    'сеть', 'сетевые запросы', 'http', 'https', 'websocket',
    'криптография', 'шифрование', 'хеширование', 'jwt',
    
    # Веб-разработка
    'верстка', 'сверстать', 'адаптивная верстка',
    'html страница', 'html разметка', 'css стили',
    'flexbox', 'grid', 'анимация', 'анимации',
    'react', 'vue', 'angular', 'jquery', 'bootstrap',
    'django', 'flask', 'fastapi', 'spring', 'laravel',
    'node.js', 'nodejs', 'express', 'nestjs',
    'rest api', 'restful', 'graphql', 'grpc',
    
    # Английские термины
    'programming', 'program', 'code', 'write code', 'coding',
    'development', 'developer', 'algorithm', 'script',
    'application', 'app', 'website', 'web development',
    'function', 'method', 'class', 'library', 'framework',
    'backend', 'frontend', 'fullstack', 'debug', 'debugging',
    'optimize', 'optimization', 'refactor', 'refactoring',
    'python', 'javascript', 'typescript', 'java', 'c++',
    'php', 'ruby', 'go', 'rust', 'html', 'css', 'sql',
    'implement', 'implementation', 'sort', 'search',
    'recursion', 'iteration', 'parse', 'parsing',
    'database', 'query', 'regex', 'asynchronous',
    'multithreading', 'network', 'http', 'encryption',
    'frontend', 'backend', 'full stack', 'api',
    
    # Фразы
    'как написать', 'как создать', 'как реализовать',
    'помоги с кодом', 'помощь с программированием',
    'исправь код', 'найди ошибку в коде', 'что не так с кодом',
    'как сделать', 'как реализовать', 'как запрограммировать',
    'how to code', 'how to program', 'help with code',
    'fix this code', 'debug this code', 'code review'
]

def select_model_for_request(messages, has_images=False, has_audio=False, has_documents=False):
    """
    Автоматический подбор модели в зависимости от типа запроса
    """
    # Проверяем наличие изображений (высший приоритет)
    if has_images:
        app.logger.info(f"Выбрана мультимодальная модель: {OLLAMA_MULTIMODAL_MODEL}")
        return OLLAMA_MULTIMODAL_MODEL, 'multimodal'
    
    # Анализируем текст запроса
    last_user_message = ""
    for msg in reversed(messages):
        if msg['role'] == 'user':
            content = msg['content']
            if isinstance(content, str):
                if content.startswith('['):
                    try:
                        parts = json.loads(content)
                        for part in parts:
                            if part.get('type') == 'text':
                                last_user_message = part['text']
                                break
                    except:
                        last_user_message = content
                else:
                    last_user_message = content
            break
    
    last_user_message_lower = last_user_message.lower()
    
    # Исключения: простые вопросы о времени НЕ считаются математикой
    simple_time_questions = [
        'который час', 'сколько времени', 'какой сегодня день',
        'какое сегодня число', 'какой день недели', 'what time is it',
        'what day is it', "what's the time", "what's the date"
    ]
    
    is_simple_time_question = any(q in last_user_message_lower for q in simple_time_questions)
    
    # Приоритет 1: Программирование (самый высокий приоритет среди текстовых)
    if any(keyword in last_user_message_lower for keyword in PROGRAMMING_KEYWORDS):
        app.logger.info(f"Выбрана модель для программирования: {OLLAMA_REASONING_MODEL} (code)")
        return OLLAMA_REASONING_MODEL, 'code'
    
    # Приоритет 2: Математика (кроме простых вопросов о времени)
    if any(keyword in last_user_message_lower for keyword in MATH_KEYWORDS) and not is_simple_time_question:
        app.logger.info(f"Выбрана модель для математики: {OLLAMA_REASONING_MODEL} (math)")
        return OLLAMA_REASONING_MODEL, 'math'
    
    # Приоритет 3: Логика и рассуждения
    if any(keyword in last_user_message_lower for keyword in LOGIC_KEYWORDS):
        app.logger.info(f"Выбрана модель для рассуждений: {OLLAMA_REASONING_MODEL} (logic)")
        return OLLAMA_REASONING_MODEL, 'logic'
    
    # Приоритет 4: Литература и тексты
    if any(keyword in last_user_message_lower for keyword in LITERATURE_KEYWORDS):
        app.logger.info(f"Выбрана модель для литературы: {OLLAMA_REASONING_MODEL} (literature)")
        return OLLAMA_REASONING_MODEL, 'literature'
    
    # По умолчанию используем обычную чат-модель
    app.logger.info(f"Выбрана стандартная чат-модель: {OLLAMA_CHAT_MODEL}")
    return OLLAMA_CHAT_MODEL, 'chat'

# -------------------------------
# Анализ сообщений для определения типа контента
# -------------------------------
def analyze_messages_for_content(messages):
    """
    Анализирует сообщения на наличие изображений, аудио, документов
    """
    has_images = False
    has_audio = False
    has_documents = False
    
    for msg in messages:
        if msg.get('file_type'):
            if msg['file_type'].startswith('image/'):
                has_images = True
            elif msg['file_type'].startswith('audio/'):
                has_audio = True
            elif msg['file_type'] in ['application/pdf', 'text/plain', 
                                     'application/msword', 
                                     'application/vnd.openxmlformats-officedocument.wordprocessingml.document']:
                has_documents = True
    
    return has_images, has_audio, has_documents

# -------------------------------
# Инициализация и миграция БД
# -------------------------------
def init_db():
    """Инициализация базы данных и миграция схемы"""
    try:
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            c = conn.cursor()
            
            # Сессии (профили пользователей)
            c.execute('''
                CREATE TABLE IF NOT EXISTS user_sessions (
                    user_id TEXT PRIMARY KEY,
                    last_session_id TEXT
                )
            ''')
            
            # Сеансы чатов
            c.execute('''
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    title TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Сообщения
            c.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    role TEXT,
                    content TEXT,
                    file_data TEXT,
                    file_type TEXT,
                    file_name TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Проверяем наличие колонки model_name в chat_sessions
            c.execute("PRAGMA table_info(chat_sessions)")
            columns = [column[1] for column in c.fetchall()]
            
            if 'model_name' not in columns:
                app.logger.info("Добавляем колонку model_name в таблицу chat_sessions")
                c.execute('ALTER TABLE chat_sessions ADD COLUMN model_name TEXT DEFAULT "auto"')
                conn.commit()
                app.logger.info("Колонка model_name успешно добавлена")
            
            # Проверяем наличие колонки model_name в messages
            c.execute("PRAGMA table_info(messages)")
            columns = [column[1] for column in c.fetchall()]
            
            if 'model_name' not in columns:
                app.logger.info("Добавляем колонку model_name в таблицу messages")
                c.execute('ALTER TABLE messages ADD COLUMN model_name TEXT')
                conn.commit()
                app.logger.info("Колонка model_name успешно добавлена в таблицу messages")
            
            conn.commit()
            app.logger.info(f"Database initialized successfully at {CHAT_DB_PATH}")
    except Exception as e:
        app.logger.error(f"Failed to initialize database: {str(e)}")
        raise

# Инициализируем БД при старте
init_db()

# Проверяем подключение к Ollama при старте
ollama_available, ollama_models = check_ollama_connection()
if not ollama_available:
    app.logger.warning("Ollama is not available. Please check if Ollama is running.")

# -------------------------------
# Вспомогательные функции
# -------------------------------
def load_users():
    users = {}
    users_file = 'users.list'
    if os.path.exists(users_file):
        with open(users_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    parts = line.strip().split(',')
                    email = parts[0]
                    password = parts[1]
                    users[email] = {'password': password}
    else:
        app.logger.warning("users.list not found, creating default user")
        users['admin@local.com'] = {'password': 'admin123'}
    return users

USERS = load_users()

def get_user_sessions(user_id):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''
            SELECT id, title, model_name, created_at, updated_at
            FROM chat_sessions
            WHERE user_id = ?
            ORDER BY updated_at DESC
        ''', (user_id,))
        return [dict(row) for row in c.fetchall()]

def get_session_messages(session_id):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # Проверяем наличие колонки model_name
        c.execute("PRAGMA table_info(messages)")
        columns = [column[1] for column in c.fetchall()]
        
        if 'model_name' in columns:
            c.execute('''
                SELECT role, content, file_data, file_type, file_name, timestamp, model_name
                FROM messages
                WHERE session_id = ?
                ORDER BY timestamp ASC
            ''', (session_id,))
        else:
            c.execute('''
                SELECT role, content, file_data, file_type, file_name, timestamp, NULL as model_name
                FROM messages
                WHERE session_id = ?
                ORDER BY timestamp ASC
            ''', (session_id,))
        
        return [dict(row) for row in c.fetchall()]

def create_session(user_id, title="Новый сеанс"):
    """Создание нового сеанса без привязки к конкретной модели"""
    session_id = str(uuid.uuid4())
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            INSERT INTO chat_sessions (id, user_id, title, model_name)
            VALUES (?, ?, ?, ?)
        ''', (session_id, user_id, title, 'auto'))
        conn.commit()
    return session_id

def update_session_title(session_id, first_message):
    """Обновить заголовок сеанса на основе первого сообщения"""
    title = first_message[:40] + ('...' if len(first_message) > 40 else '')
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            UPDATE chat_sessions
            SET title = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (title, session_id))
        conn.commit()

def save_message(session_id, role, content, file_data=None, file_type=None, file_name=None, model_name=None):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        
        # Проверяем наличие колонки model_name в messages
        c.execute("PRAGMA table_info(messages)")
        columns = [column[1] for column in c.fetchall()]
        
        # Вставляем сообщение с учетом наличия колонки model_name
        if 'model_name' in columns and role == 'assistant' and model_name:
            c.execute('''
                INSERT INTO messages (session_id, role, content, file_data, file_type, file_name, model_name)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (session_id, role, content, file_data, file_type, file_name, model_name))
        else:
            c.execute('''
                INSERT INTO messages (session_id, role, content, file_data, file_type, file_name)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (session_id, role, content, file_data, file_type, file_name))
        
        c.execute('''
            UPDATE chat_sessions
            SET updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (session_id,))
        conn.commit()

def get_last_session(user_id):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT last_session_id FROM user_sessions WHERE user_id = ?', (user_id,))
        row = c.fetchone()
        return row[0] if row else None

def set_last_session(user_id, session_id):
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO user_sessions (user_id, last_session_id)
            VALUES (?, ?)
        ''', (user_id, session_id))
        conn.commit()

# -------------------------------
# Маршруты аутентификации
# -------------------------------
@app.route('/')
def index():
    if 'email' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('chat'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        if not email or not password:
            return render_template('login.html', error='Все поля обязательны')
        
        if email in USERS and USERS[email]['password'] == password:
            session['email'] = email
            return redirect(url_for('chat'))
        else:
            return render_template('login.html', error='Неверный email или пароль')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    if 'email' in session and 'current_session' in session:
        set_last_session(session['email'], session['current_session'])
    session.clear()
    return redirect(url_for('login'))

# -------------------------------
# Основной чат
# -------------------------------
@app.route('/chat')
def chat():
    if 'email' not in session:
        return redirect(url_for('login'))
    
    user_id = session['email']
    sessions = get_user_sessions(user_id)
    
    if not session.get('current_session'):
        last_id = get_last_session(user_id)
        if last_id and any(s['id'] == last_id for s in sessions):
            session['current_session'] = last_id
        elif sessions:
            session['current_session'] = sessions[0]['id']
        else:
            new_id = create_session(user_id)
            session['current_session'] = new_id
            sessions = get_user_sessions(user_id)
    
    return render_template('chat.html', 
                         sessions=sessions, 
                         current_session=session.get('current_session'),
                         footer_text=FOOTER_TEXT)

# -------------------------------
# API для работы с сеансами
# -------------------------------
@app.route('/api/sessions', methods=['GET'])
def api_get_sessions():
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    user_id = session['email']
    sessions = get_user_sessions(user_id)
    return jsonify(sessions)

@app.route('/api/sessions/<session_id>/messages', methods=['GET'])
def api_get_messages(session_id):
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    messages = get_session_messages(session_id)
    return jsonify(messages)

@app.route('/api/sessions/<session_id>/switch', methods=['POST'])
def api_switch_session(session_id):
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    session['current_session'] = session_id
    set_last_session(session['email'], session_id)
    return jsonify({'status': 'ok'})

@app.route('/api/sessions/<session_id>/model-info', methods=['GET'])
def api_get_session_model(session_id):
    """Получить информацию о модели, используемой в сеансе"""
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        try:
            c.execute('SELECT model_name FROM chat_sessions WHERE id = ?', (session_id,))
            row = c.fetchone()
            
            if row:
                return jsonify({'model_name': row[0]})
            else:
                return jsonify({'model_name': 'auto'})
        except sqlite3.OperationalError:
            # Если колонка все еще не существует, возвращаем 'auto'
            return jsonify({'model_name': 'auto'})

@app.route('/api/sessions/new', methods=['POST'])
def api_new_session():
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user_id = session['email']
    session_id = create_session(user_id)
    session['current_session'] = session_id
    set_last_session(user_id, session_id)
    return jsonify({'id': session_id, 'title': 'Новый сеанс'})

# -------------------------------
# API для Ollama
# -------------------------------
@app.route('/api/ollama/status', methods=['GET'])
def api_ollama_status():
    """Проверка статуса Ollama"""
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    available, models = check_ollama_connection()
    return jsonify({
        'available': available,
        'models': [m['name'] for m in models] if available else []
    })

# -------------------------------
# API для получения подписи футера
# -------------------------------
@app.route('/api/footer-text', methods=['GET'])
def api_footer_text():
    """Возвращает текст подписи для футера"""
    return FOOTER_TEXT

# -------------------------------
# Отладочный эндпоинт для проверки часового пояса
# -------------------------------
@app.route('/api/timezone-info', methods=['GET'])
def api_timezone_info():
    """Возвращает информацию о текущем часовом поясе (только для разработки)"""
    if 'email' not in session and app.debug == False:
        return jsonify({'error': 'Доступ запрещен'}), 403
    
    return jsonify(get_timezone_info())

# -------------------------------
# ОТПРАВКА СООБЩЕНИЯ (ИСПРАВЛЕННАЯ ВЕРСИЯ)
# -------------------------------
@app.route('/send_message', methods=['POST'])
def send_message():
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user_id = session['email']
    session_id = session.get('current_session')
    
    if not session_id:
        session_id = create_session(user_id)
        session['current_session'] = session_id
    
    message_text = ""
    file_data = None
    file_type = None
    file_name = None
    file_size = 0
    
    if 'multipart/form-data' in request.content_type:
        message_text = request.form.get('message', '')
        if 'file' in request.files:
            file = request.files['file']
            if file.filename:
                file.seek(0, os.SEEK_END)
                file_size = file.tell()
                file.seek(0)
                
                file_data = base64.b64encode(file.read()).decode('utf-8')
                file_type = file.content_type or mimetypes.guess_type(file.filename)[0] or 'application/octet-stream'
                file_name = file.filename
    else:
        data = request.get_json()
        message_text = data.get('message', '')
    
    # ПОЛУЧАЕМ ТЕКУЩЕЕ ВРЕМЯ В УКАЗАННОМ ЧАСОВОМ ПОЯСЕ
    current_time_str = get_current_time_in_timezone()
    
    # Логируем используемый часовой пояс для отладки
    app.logger.info(f"Используется часовой пояс: {TIMEZONE_STR}, время: {current_time_str}")
    
    # Проверяем, является ли это первым сообщением в сеансе
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM messages WHERE session_id = ?', (session_id,))
        msg_count = c.fetchone()[0]
        is_first_message = (msg_count == 0)
    
    # АНАЛИЗ ТИПА ФАЙЛА И ФОРМИРОВАНИЕ СООБЩЕНИЯ
    final_message_text = ""
    selected_model = None
    model_category = 'chat'
    has_image = False
    image_validation_error = None
    
    # Сохраняем исходное сообщение пользователя (для отображения в интерфейсе)
    user_content = []
    if message_text:
        user_content.append({"type": "text", "text": message_text})
    
    # Проверяем, есть ли файл
    if file_data:
        # Проверяем, поддерживается ли файл как изображение
        is_valid_image, validation_error = validate_image_file(file_data, file_type, file_name, file_size)
        
        if is_valid_image:
            has_image = True
            # Добавляем информацию о файле в user_content
            user_content.append({
                "type": "file", 
                "file_data": file_data, 
                "file_type": file_type, 
                "file_name": file_name
            })
            
            # ФОРМИРУЕМ ПРОМПТ ДЛЯ МОДЕЛИ
            if message_text.strip():
                # СЛУЧАЙ 1: Есть и текст, и изображение
                final_message_text = f"Текущее время: {current_time_str}. Подпись под изображением: {message_text}"
            else:
                # СЛУЧАЙ 2: Только изображение, без текста
                final_message_text = f"Текущее время: {current_time_str}. Ответ - на русском языке. Списком перечисли все предметы на изображении. Опиши само изображение и всё, что можно про него рассказать. Не задавай вопросов. Не пиши о том, чего нет на изображении."
            
            selected_model = OLLAMA_MULTIMODAL_MODEL
            model_category = 'multimodal'
        else:
            # СЛУЧАЙ 4: Неподдерживаемый тип файла или превышены ограничения
            bot_reply = f"⚠️ {validation_error}"
            
            # Добавляем информацию о файле в user_content для истории
            user_content.append({
                "type": "file", 
                "file_data": file_data, 
                "file_type": file_type, 
                "file_name": file_name
            })
            
            # Сохраняем сообщение пользователя (для истории)
            save_message(session_id, 'user', json.dumps(user_content, ensure_ascii=False) if user_content else message_text,
                        file_data, file_type, file_name)
            
            # Сохраняем ответ-уведомление
            save_message(session_id, 'assistant', bot_reply, model_name='system')
            
            # Обновляем заголовок для первого сообщения
            if is_first_message and message_text:
                update_session_title(session_id, message_text)
            
            return jsonify({
                'response': bot_reply,
                'session_id': session_id,
                'model_used': 'system',
                'response_time': 0,
                'assistant_timestamp': datetime.now().isoformat()
            })
    else:
        # СЛУЧАЙ 3: Только текст, без файлов
        if message_text.strip():
            user_content.append({"type": "text", "text": message_text})
            final_message_text = f"Текущее время: {current_time_str}. Дай краткий, точный ответ на русском языке. Не добавляй рассуждений.\n\nВопрос пользователя: {message_text}"
        else:
            # Пустое сообщение
            return jsonify({'error': 'Пустое сообщение'}), 400
        
        selected_model = OLLAMA_CHAT_MODEL
        model_category = 'chat'
    
    # Сохраняем сообщение пользователя (для отображения в интерфейсе)
    save_message(session_id, 'user', json.dumps(user_content, ensure_ascii=False) if user_content else message_text,
                 file_data if has_image else None, 
                 file_type if has_image else None, 
                 file_name if has_image else None)
    
    # Получаем время отправки сообщения пользователя
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            SELECT timestamp FROM messages 
            WHERE session_id = ? AND role = 'user' 
            ORDER BY timestamp DESC LIMIT 1
        ''', (session_id,))
        row = c.fetchone()
        user_timestamp = row[0] if row else None
    
    # Обновляем заголовок для первого сообщения
    if is_first_message and message_text:
        update_session_title(session_id, message_text)
    
    # Получаем историю (нужна для контекста)
    history = get_session_messages(session_id)
    
    # Определяем тип контента для выбора модели (только для текстовых запросов)
    if not has_image:
        has_images, has_audio, has_documents = analyze_messages_for_content(history)
        selected_model, model_category = select_model_for_request(history, has_images, has_audio, has_documents)
    
    app.logger.info(f"Session {session_id}: выбрана модель {selected_model} (категория: {model_category})")
    
    # ЗАМЕР ВРЕМЕНИ ВЫПОЛНЕНИЯ
    start_time = time.time()
    
    # ПОДГОТОВКА ЗАПРОСА К OLLAMA
    if has_image:
        # Для изображений нужно создать специальную структуру сообщения
        # Находим последнее сообщение пользователя с изображением
        last_user_msg = None
        for msg in reversed(history):
            if msg['role'] == 'user' and msg.get('file_data'):
                last_user_msg = msg
                break
        
        if last_user_msg:
            # Создаем сообщение для Ollama с изображением
            ollama_messages = []
            
            # Добавляем только текущее сообщение с изображением (без истории)
            # Это предотвращает путаницу и повторную отправку изображений
            ollama_messages.append({
                'role': 'user',
                'content': final_message_text,
                'images': [last_user_msg['file_data']]  # Изображение в base64
            })
            
            app.logger.info(f"Отправка запроса с изображением к модели {selected_model}")
            
            # Отправляем запрос
            bot_reply = call_ollama_chat(ollama_messages, model=selected_model)
        else:
            bot_reply = "⚠️ Ошибка: не удалось найти изображение для обработки"
    else:
        # Для текстовых запросов
        ollama_messages = [{'role': 'user', 'content': final_message_text}]
        app.logger.info(f"Отправка текстового запроса к модели {selected_model}")
        bot_reply = call_ollama_chat(ollama_messages, model=selected_model)
    
    end_time = time.time()
    response_time = round(end_time - start_time, 1)
    
    # Сохраняем ответ с указанием использованной модели
    save_message(session_id, 'assistant', bot_reply, model_name=selected_model)
    
    # Получаем timestamp сохраненного ответа
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            SELECT timestamp FROM messages 
            WHERE session_id = ? AND role = 'assistant' 
            ORDER BY timestamp DESC LIMIT 1
        ''', (session_id,))
        row = c.fetchone()
        assistant_timestamp = row[0] if row else None
    
    return jsonify({
        'response': bot_reply,
        'session_id': session_id,
        'model_used': selected_model,
        'model_category': model_category,
        'response_time': response_time,
        'user_timestamp': user_timestamp,
        'assistant_timestamp': assistant_timestamp
    })

# -------------------------------
# Очистка истории сеанса
# -------------------------------
@app.route('/clear_history', methods=['POST'])
def clear_history():
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    session_id = session.get('current_session')
    if not session_id:
        return jsonify({'error': 'Нет активного сеанса'}), 400
    
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('DELETE FROM messages WHERE session_id = ?', (session_id,))
        c.execute('UPDATE chat_sessions SET title = ? WHERE id = ?', ('Новый сеанс', session_id))
        conn.commit()
    
    return jsonify({'status': 'ok'})

# -------------------------------
# Удаление сеанса
# -------------------------------
@app.route('/api/sessions/<session_id>/delete', methods=['POST'])
def api_delete_session(session_id):
    if 'email' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    user_id = session['email']
    
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        c = conn.cursor()
        c.execute('SELECT user_id FROM chat_sessions WHERE id = ?', (session_id,))
        row = c.fetchone()
        
        if not row:
            return jsonify({'error': 'Сеанс не найден'}), 404
        
        if row[0] != user_id:
            return jsonify({'error': 'Нет прав на удаление этого сеанса'}), 403
        
        c.execute('DELETE FROM messages WHERE session_id = ?', (session_id,))
        c.execute('DELETE FROM chat_sessions WHERE id = ?', (session_id,))
        
        c.execute('SELECT COUNT(*) FROM chat_sessions WHERE user_id = ?', (user_id,))
        count = c.fetchone()[0]
        
        if count == 0:
            c.execute('DELETE FROM user_sessions WHERE user_id = ?', (user_id,))
        else:
            c.execute('SELECT last_session_id FROM user_sessions WHERE user_id = ?', (user_id,))
            row = c.fetchone()
            if row and row[0] == session_id:
                c.execute('SELECT id FROM chat_sessions WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1', (user_id,))
                new_last = c.fetchone()
                if new_last:
                    c.execute('UPDATE user_sessions SET last_session_id = ? WHERE user_id = ?', (new_last[0], user_id))
                else:
                    c.execute('DELETE FROM user_sessions WHERE user_id = ?', (user_id,))
        
        conn.commit()
    
    if session.get('current_session') == session_id:
        session.pop('current_session', None)
    
    return jsonify({'status': 'ok'})

# -------------------------------
# Статика и прочее
# -------------------------------
@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.context_processor
def inject_footer():
    """Контекст-процессор для передачи подписи в шаблоны"""
    return {
        'footer_content': FOOTER_TEXT
    }

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)