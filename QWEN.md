# ИИ Локальный — QWEN.md

## Обзор проекта

**ИИ Локальный v3.3** — это автономное веб-приложение для локального развёртывания, предоставляющее чат-интерфейс для работы с ИИ-моделями через Ollama и генерации изображений через Automatic1111 (Stable Diffusion).

### Основные возможности

- **Чат с ИИ**: Маршрутизация запросов между различными моделями в зависимости от типа задачи
- **Мультимодальный анализ**: Обработка и анализ изображений
- **Генерация изображений**: Интеграция с Automatic1111 для создания изображений
- **Сохранение сессий**: История чатов сохраняется в JSON-файлах
- **Docker-развёртывание**: Полная контейнеризация для простоты установки

### Архитектура

```
barvalailocalsite/
├── backend/              # Python FastAPI приложение
│   ├── app.py           # Точка входа, основные эндпоинты
│   ├── router.py        # Маршрутизация запросов к ИИ
│   ├── Dockerfile       # Конфигурация контейнера
│   ├── requirements.txt # Python зависимости
│   ├── models/          # Обработчики моделей
│   │   ├── chat_handler.py      # Чат-логика
│   │   ├── image_generator.py   # Генерация изображений
│   │   ├── multimodal_handler.py # Анализ изображений
│   │   ├── reasoning_handler.py # Сложные запросы
│   │   ├── router_handler.py    # Логика маршрутизации
│   │   ├── camera_handler.py    # Работа с камерой
│   │   └── prompts.py           # Системные промпты
│   ├── utils/           # Утилиты
│   │   ├── config.py          # Настройки из .env
│   │   ├── ollama_client.py   # Клиент Ollama API
│   │   ├── a1111_client.py    # Клиент Automatic1111 API
│   │   └── file_utils.py      # Работа с файлами
│   └── storage/         # Хранилище (сессии, загрузки, кэш)
├── frontend/
│   └── index.html       # Single-page приложение (HTML/CSS/JS)
├── docker-compose.yml   # Docker Compose конфигурация
├── .env.example         # Шаблон переменных окружения
├── init.sh              # Скрипт первичной настройки
└── QWEN.md              # Этот файл
```

## Технологии

| Компонент | Технология |
|-----------|------------|
| Backend | Python 3.11, FastAPI, Uvicorn |
| Frontend | Vanilla JS, CSS (без фреймворков) |
| Контейнеризация | Docker, Docker Compose |
| ИИ-модели | Ollama (Qwen3, Qwen3-VL) |
| Генерация изображений | Automatic1111 (Stable Diffusion) |
| Конфигурация | Pydantic Settings, .env |

## Сборка и запуск

### Предварительные требования

- Docker и Docker Compose
- Ollama (локально, для доступа к моделям)
- Automatic1111 (опционально, для генерации изображений)

### Быстрый старт

```bash
# 1. Клонирование и переход в директорию
cd /home/GIT/GITEA/BARVAL-MY/barvalailocalsite/

# 2. Запуск скрипта инициализации
bash init.sh

# 3. Открыть в браузере
# http://localhost:5000 (или порт из APP_PORT)
```

### Ручной запуск

```bash
# Сборка и запуск
docker compose up -d --build

# Просмотр логов
docker compose logs -f ai-local

# Перезапуск
docker compose restart ai-local

# Остановка
docker compose down
```

### Конфигурация (.env)

Основные переменные окружения:

```bash
# Системные
TIMEZONE=Europe/Moscow
APP_PORT=5000
SECRET_KEY=change_this_in_production

# Ollama
OLLAMA_URL=http://host.docker.internal:11434
LLM_CHAT_MODEL=qwen3:4b-instruct-2507-q4_K_M
LLM_MULTIMODAL_MODEL=qwen3-vl:8b-instruct-q4_K_M
LLM_REASONING_MODEL=qwen3:8b-q4_K_M

# Automatic1111
AUTOMATIC1111_URL=http://host.docker.internal:7860
AUTOMATIC1111_MODEL=cyberrealisticXL_v90.safetensors
```

## Эндпоинты API

| Метод | Эндпоинт | Описание |
|-------|----------|----------|
| GET | `/` | Фронтенд приложение |
| GET | `/health` | Проверка здоровья |
| POST | `/api/chat` | Чат с ИИ |
| POST | `/api/upload` | Загрузка файла |
| POST | `/api/generate-image` | Генерация изображения |
| GET | `/api/sessions` | Список сессий |
| GET | `/api/session/{id}` | Получить сессию |
| DELETE | `/api/session/{id}` | Удалить сессию |

## Структура моделей Ollama

Проект использует три типа моделей:

1. **Чат-модель** (`qwen3:4b-instruct-2507-q4_K_M`)
   - Простые запросы, маршрутизация
   - Температура: 0.1, Top-P: 0.1

2. **Мультимодальная модель** (`qwen3-vl:8b-instruct-q4_K_M`)
   - Анализ изображений
   - Температура: 0.7, Top-P: 0.9

3. **Reasoning модель** (`qwen3:8b-q4_K_M`)
   - Сложные логические запросы
   - Температура: 0.7, Top-P: 0.9

## Разработка

### Добавление новых зависимостей

```bash
# Добавить в backend/requirements.txt
echo "new-package==1.0.0" >> backend/requirements.txt

# Пересобрать контейнер
docker compose up -d --build
```

### Тестирование API

```bash
# Проверка здоровья
curl http://localhost:5000/health

# Тест чата
curl -X POST http://localhost:5000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Привет!", "session_id": "test"}'
```

### Логирование

Приложение использует Loguru для логирования. Логи выводятся в stderr и доступны через:

```bash
docker compose logs -f ai-local
```

## Примечания

- Фронтенд встраивается непосредственно в бэкенд при сборке Docker
- Сессии хранятся в `backend/storage/sessions/` в формате JSON
- Загруженные файлы сохраняются в `backend/storage/uploads/`
- Для Linux может потребоваться изменить `OLLAMA_URL` на `http://172.17.0.1:11434`

## Автор

**Барсуков Валерий**, 2026

Лицензия: Проприетарное ПО
