<div align="center">
  <img src="docs/logo.png" alt="Полностью Локальный ИИ (ПЛИИ)" width="200">

  # Полностью Локальный ИИ (ПЛИИ)
  
  **ПЛИИ - полностью локальный персональный ассистент на основе искусственного интеллекта.**  
  **Запустите свой собственный стек ИИ полностью на собственном оборудовании без привязки к облаку.**  
  
  [![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
  [![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
  [![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?logo=docker&logoColor=white)](https://www.docker.com/)

[English](README.md) | [Русский](README-ru.md)
</div>

---

## ✨ Возможности

### 🤖 Основные возможности ИИ
- 💬 **Интеллектуальный чат** – общение с локальными LLM через Ollama с умной маршрутизацией запросов (быстрые модели для простых вопросов, мощные — для сложных рассуждений)
- 🧠 **Продвинутое рассуждение** – выделенная модель для сложных задач: вычисления, генерация кода, творческое письмо
- 🔍 **Мультимодальный анализ** – загрузка изображений и вопросы по их содержанию с использованием моделей с поддержкой зрения
- 🎨 **Генерация изображений** – создание картинок по текстовому описанию через Stable Diffusion (Automatic1111) с автоматической оптимизацией промптов
- 🎤 **Распознавание речи** – преобразование голосовых сообщений и аудиофайлов в текст с помощью Whisper ASR
- 🗣️ **Синтез речи** – озвучивание ответов ассистента через Piper TTS (выбор мужского/женского голоса)

### 📁 Управление документами и знаниями
- 📚 **RAG с Qdrant** – загрузка документов (PDF, DOC, DOCX, TXT) и вопросы по их содержанию с использованием семантического поиска
- 🗂️ **Сеансы чата** – ведение множества независимых диалогов с авто-озаглавливанием и индикаторами непрочитанных сообщений
- 💾 **Экспорт чатов** – сохранение любого диалога в автономный HTML-файл с встроенными медиафайлами

### 🏠 Интеграция с домом (опционально)
- 📹 **Видеонаблюдение** – запрос снимков с IP-камер и их анализ с помощью мультимодальных моделей
- 🔐 **Контроль доступа** – гранулярные права доступа к камерам для каждого пользователя через панель администратора

### 👥 Пользовательский опыт
- 🌐 **Мультиязычность** – полный интерфейс и ответы ИИ на русском и английском языках
- 🌓 **Тёмная/светлая тема** – переключение тем с сохранением предпочтений
- 🎚️ **Выбор голоса** – мужской или женский голос для ответов через TTS
- 📊 **Очередь запросов** – отслеживание статуса в реальном времени с индикацией позиции в очереди
- 📎 **Вложения файлов** – поддержка изображений, аудиофайлов и документов в диалогах

### ⚙️ Администрирование
- 👤 **Управление пользователями** – добавление, редактирование, удаление пользователей; смена паролей; назначение классов обслуживания
- 🔑 **Права на камеры** – контроль доступа пользователей к конкретным камерам
- 📈 **Мониторинг системы** – просмотр размеров баз данных и системной статистики
- 🔧 **CLI-инструменты** – управление паролем администратора через Flask CLI

### 🔒 Конфиденциальность и безопасность
- 🏠 **100% локально** – вся обработка происходит на вашем оборудовании; данные никогда не покидают вашу сеть
- 🔐 **Аутентификация по сессиям** – безопасная авторизация пользователей с хешированием паролей
- 🛡️ **Контроль доступа к файлам** – загруженные файлы доступны только авторизованным пользователям
- 🧹 **Изоляция данных** – сеансы, сообщения и документы каждого пользователя строго разделены

---

## 🧱 Архитектура

ПЛИИ — это модульное веб-приложение на Flask, которое координирует несколько самостоятельно размещённых ИИ-сервисов.

### Основные компоненты

| Компонент | Назначение | Технология |
|-----------|------------|------------|
| **Flask** | Веб-фреймворк, маршрутизация, шаблоны | Python |
| **Ollama** | Локальный инференс LLM (чат, рассуждения, мультимодальность) | Go + llama.cpp |
| **Automatic1111** | Генерация изображений через Stable Diffusion | Python + PyTorch |
| **Whisper ASR** | Распознавание речи (транскрибация) | OpenAI Whisper / faster-whisper |
| **Piper TTS** | Синтез речи (текст в голос) | ONNX + Piper |
| **Qdrant** | Векторная база данных для семантического поиска (RAG) | Rust |
| **Redis** | Управление очередью запросов для асинхронной обработки | C |
| **SQLite** | Учётные записи, сеансы, сообщения, документы | Встраиваемая СУБД |

Все компоненты могут работать в Docker-контейнерах с единой сетевой конфигурацией.

---

## 📋 Системные требования

### Рекомендуемое оборудование
| Компонент | Минимум | Рекомендуется |
|-----------|---------|---------------|
| **ОЗУ** | 8 ГБ | 16–32 ГБ (для больших моделей) |
| **ЦПУ** | 4 ядра | 8+ ядер |
| **ГПУ** | Опционально | NVIDIA с CUDA (для ускорения) |
| **Хранилище** | 20 ГБ | 100+ ГБ (для моделей и данных пользователей) |

### Программные требования
- Сервер с Linux (или Windows/macOS с Docker Desktop)
- Docker Engine ≥ 20.10
- Docker Compose ≥ 2.0
- Подключение к интернету (только для первоначальной загрузки моделей)

> 💡 **Примечание**: После загрузки моделей ПЛИИ работает полностью офлайн.

---

## 🚀 Быстрый старт

### 1. Клонирование репозитория
```bash
git clone https://github.com/barval/flai.git
cd flai
```

### 2. Настройка окружения
```bash
cp .env.example .env
# Отредактируйте .env с нужными настройками (см. раздел Конфигурация)
```

### 3. Запуск приложения
```bash
docker-compose up -d
```
Веб-интерфейс станет доступен по адресу `http://localhost:5000`.

### 4. Создание учётной записи администратора
```bash
docker exec -it flai-web-1 flask admin-password ВашНадёжныйПароль123
```
Вход в систему:
- Логин: `admin`
- Пароль: `указанный вами пароль`

---

## 🔧 Настройка зависимых сервисов
ПЛИИ интегрируется с несколькими внешними ИИ-сервисами. Ниже приведены примеры Docker Compose для их запуска вместе с основным приложением.
Также смотрите примеры в папке `services`.
- ⚠️ Важно: Все сервисы должны использовать одну Docker-сеть (flai_network) для корректного взаимодействия.

### Создание общей сети
```bash
docker network create flai_network
```

### 🤖 Ollama (сервер LLM)
```yaml
# services/ollama/docker-compose.yml
services:
  ollama:
    image: ollama/ollama:latest
    container_name: ollama
    networks:
      - flai_network
    ports:
      - "11434:11434"
    volumes:
      - ollama:/root/.ollama
    environment:
      - OLLAMA_REQUEST_TIMEOUT=1200s
      - OLLAMA_MAX_LOADED_MODELS=1
      - OLLAMA_KEEP_ALIVE=0
    # Раскомментируйте для поддержки ГПУ:
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: 1
    #           capabilities: [gpu]

volumes:
  ollama:
    external: true
    name: ollama

networks:
  flai_network:
    external: true
```

Загрузка необходимых моделей:
```bash
docker exec ollama ollama pull qwen3:4b-instruct-2507-q4_K_M      # Чат/Маршрутизатор
docker exec ollama ollama pull qwen3-vl:8b-instruct-q4_K_M        # Мультимодальная
docker exec ollama ollama pull gpt-oss:20b                        # Рассуждения
docker exec ollama ollama pull bge-m3:latest                      # Эмбеддинги (RAG)
```

### 🎨 Automatic1111 (Stable Diffusion)
```yaml
# services/automatic1111/docker-compose.yml
services:
  automatic1111:
    image: siutin/stable-diffusion-webui-docker:latest-cuda  # Используйте -cpu для CPU
    container_name: sd-webui
    networks:
      - flai_network
    ports:
      - "7860:7860"
    volumes:
      - ./models:/app/stable-diffusion-webui/models
      - ./embeddings:/app/stable-diffusion-webui/embeddings
      - ./outputs:/app/stable-diffusion-webui/outputs
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
      - NVIDIA_DRIVER_CAPABILITIES=compute,utility
      - NVIDIA_REQUIRE_CUDA=cuda>=12.1
      - PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
    runtime: nvidia
    command:
      - /app/stable-diffusion-webui/webui.sh
      - --listen
      - --port=7860
      - --api
      - --api-log
      - --opt-sdp-attention
      - --medvram
      - --medvram-sdxl

networks:
  flai_network:
    external: true
```
Поместите ваш чекпоинт Stable Diffusion (например, `cyberrealisticXL_v90.safetensors`) в каталог `./models`.
Найти чекпоит можно тут: `https://civitai.com/`

### 🎤 Whisper ASR (распознавание речи)
```yaml
# services/openai-whisper/docker-compose.yml
services:
  openai-whisper:
    image: onerahmet/openai-whisper-asr-webservice:latest        # CPU
    # image: onerahmet/openai-whisper-asr-webservice:latest-gpu  # GPU
    container_name: openai-whisper
    networks:
      - flai_network
    ports:
      - "9000:9000"
    environment:
      ASR_MODEL: "medium"                  # Варианты: tiny, base, small, medium, large
      ASR_ENGINE: "faster_whisper"         # Рекомендуется для производительности
      ASR_DEVICE: "cpu"                    # Измените на "cuda" для ГПУ
    volumes:
      - ~/.cache/huggingface:/root/.cache/huggingface
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: always

networks:
  flai_network:
    external: true
```
### 🗣️ Piper TTS (синтез речи)
```yaml
# services/piper/docker-compose.yml
services:
  piper:
    build:
      context: ./services/piper
      dockerfile: Dockerfile.piper
    container_name: piper
    networks:
      - flai_network
    ports:
      - "18888:8888"
    volumes:
      - ./piper_models:/app/models
    environment:
      - PIPER_MODEL_DIR=/app/models
    restart: unless-stopped

networks:
  flai_network:
    external: true
```

Загрузка голосовых моделей:
```text
# Голоса Piper TTS для скачивания
# Формат: HuggingFace URL

# Русский мужской голос
https://huggingface.co/rhasspy/piper-voices/blob/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx
https://huggingface.co/rhasspy/piper-voices/blob/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json

# Русский женский голос
https://huggingface.co/rhasspy/piper-voices/blob/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx
https://huggingface.co/rhasspy/piper-voices/blob/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json

# Английский мужской голос
https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx
https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json

# Английский женский голос
https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx
https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx.json
```

### 🗄️ Qdrant (векторная база данных для RAG)
```yaml
# services/qdrant/docker-compose.yml
services:
  qdrant:
    image: qdrant/qdrant:latest
    container_name: qdrant
    networks:
      - flai_network
    ports:
      - "6333:6333"   # HTTP API
      - "6334:6334"   # gRPC API (опционально)
    volumes:
      - qdrant_data:/qdrant/storage
    environment:
      QDRANT__SERVICE__API_KEY: ${QDRANT_API_KEY}
      QDRANT__SERVICE__ENABLE_TLS: 0  # Отключить TLS для локальной разработки

volumes:
  qdrant_data:
    external: true
    name: qdrant_data

networks:
  flai_network:
    external: true
```

## ⚙️ Конфигурация (.env)
Все настройки задаются в файле `.env`. Ключевые переменные:

### Основные настройки
| Переменная | Описание | Пример |
|------------|----------|--------|
| `SECRET_KEY` | Секрет для сессий Flask (сгенерируйте надёжное случайное значение) | `x8#kL9$mP2@vN5!qR` |
| `TIMEZONE` | Локальный часовой пояс для временных меток | `Europe/Moscow` |
| `REDIS_URL` | Строка подключения к Redis | `redis://redis:6379/0` |

### Модели LLM (Ollama)
| Переменная | Описание | Пример |
|------------|----------|--------|
| `OLLAMA_URL` | Адрес API Ollama | `http://host.docker.internal:11434` |
| `LLM_CHAT_MODEL` | Быстрая модель для чата/маршрутизации | `qwen3:4b-instruct-2507-q4_K_M` |
| `LLM_MULTIMODAL_MODEL` | Модель с поддержкой зрения | `qwen3-vl:8b-instruct-q4_K_M` |
| `LLM_REASONING_MODEL` | Мощная модель для сложных задач | `gpt-oss:20b` |
| `LLM_*_CONTEXT_WINDOW` | Размер контекстного окна (токены) | `8192`, `16384`, `32768` |
| `LLM_*_TEMPERATURE` | Креативность/случайность (0.0–1.0) | `0.1` (чат), `0.7` (рассуждения) |
| `LLM_*_TOP_P` | Параметр nucleus sampling | `0.1`, `0.9` |
| `LLM_*_TIMEOUT` | Таймаут запроса в секундах | `60`, `120`, `300` |

### Генерация изображений (Automatic1111)
| Переменная | Описание | Пример |
|------------|----------|--------|
| `AUTOMATIC1111_URL` | Адрес API WebUI | `http://host.docker.internal:7860` |
| `AUTOMATIC1111_MODEL` | Имя файла чекпоинта | `cyberrealisticXL_v90.safetensors` |
| `AUTOMATIC1111_TIMEOUT` | Таймаут генерации (секунды) | `180` |
| `MAX_IMAGE_WIDTH` / `HEIGHT` | Максимальное разрешение вывода | `3840`, `2160` |
| `MAX_IMAGE_SIZE_MB` | Максимальный размер загрузки | `5` |

### Обработка аудио
| Переменная | Описание | Пример |
|------------|----------|--------|
| `WHISPER_API_URL` | Адрес API Whisper ASR | `http://host.docker.internal:9000/asr` |
| `WHISPER_API_TIMEOUT` | Таймаут транскрибации | `120` |
| `PIPER_URL` | Адрес API Piper TTS | `http://piper:8888/tts` |
| `PIPER_TIMEOUT` | Таймаут синтеза речи | `30` |
| `MAX_VOICE_SIZE_MB` | Макс. размер голосовой записи | `5` |
| `MAX_AUDIO_SIZE_MB` | Макс. размер загружаемого аудио | `5` |

### Интеграция с камерами (опционально)
| Переменная | Описание | Пример |
|------------|----------|--------|
| `CAMERA_API_URL` | Адрес сервиса камер | `http://host.docker.internal:5005` |
| `CAMERA_ENABLED` | Включить/отключить модуль камер | `true` / `false` |
| `CAMERA_API_TIMEOUT` | Таймаут запроса снимка (сек) | `15` |
| `CAMERA_CHECK_INTERVAL` | Интервал проверки доступности (сек) | `30` |
API для получения снимков с камер видеонаблюдения в различных комнатах: `https://github.com/barval/room-snapshot-api`

### RAG / Qdrant
| Переменная | Описание | Пример |
|------------|----------|--------|
| `QDRANT_URL` | Адрес HTTP API Qdrant | `http://host.docker.internal:6333` |
| `QDRANT_API_KEY` | API-ключ для аутентификации | `ваш_надёжный_ключ` |
| `EMBEDDING_MODEL` | Модель эмбеддингов Ollama | `bge-m3:latest` |
| `RAG_CHUNK_SIZE` | Размер текстового чанка для индексации | `500` |
| `RAG_CHUNK_OVERLAP` | Перекрытие между чанками | `50` |
| `RAG_TOP_K` | Количество чанков для поиска | `10` |

### Настройки файлов и документов
| Переменная | Описание | Пример |
|------------|----------|--------|
| `MAX_DOCUMENT_SIZE_MB` | Макс. размер загружаемого документа | `25` |
| `UPLOAD_FOLDER` | Путь для загруженных медиафайлов | `data/uploads` |
| `DOCUMENTS_FOLDER` | Путь для загруженных документов | `data/documents` |

### Продвинутые / отладочные
| Переменная | Описание | Пример |
|------------|----------|--------|
| `TOKEN_CHARS` | Оцен. кол-во символов на токен для расчёта контекста | `3` |
| `CONTEXT_HISTORY_PERCENT` | % контекстного окна под историю диалога | `75` |
| `DEBUG_TRANSLATIONS` | Включить отладку локализации | `false` |

---

## 👥 Управление пользователями

### Панель администратора (/admin)
- 👤 Операции с пользователями: создание, редактирование, удаление учётных записей
- 🔑 Управление паролями: сброс паролей для любого пользователя
- 🔐 Права на камеры: предоставление/отзыв доступа к конкретным камерам для каждого пользователя
- 📊 Системная статистика: мониторинг размеров баз данных (пользователи, чаты, файлы, документы)
- 🎚️ Классы обслуживания: назначение уровней приоритета (0=высший, 2=низший) для обработки очереди

### CLI-команды
```bash
# Установка или смена пароля администратора
docker exec -it flai-web-1 flask admin-password НовыйПароль123
```
### Самообслуживание пользователя
- 🌐 Переключение языка: выбор между русским и английским интерфейсом
- 🌓 Смена темы: переключение между светлой/тёмной темами (сохраняется для пользователя)
- 🎚️ Пол голоса: выбор мужского/женского голоса для ответов через TTS
- 📁 Загрузка документов: добавление файлов PDF/DOC/TXT для вопросов через RAG
- 💾 Экспорт чата: сохранение диалогов в автономные HTML-файлы

---

## 🗺️ Дорожная карта

### ✅ Завершено
- Маршрутизация запросов по моделям (простые → быстрая модель, сложные → рассуждения)
- Мультимодальный анализ изображений с историей диалога
- Генерация изображений с автоматической оптимизацией промптов
- Распознавание (Whisper) и синтез (Piper TTS) речи
- Загрузка документов + RAG с семантическим поиском через Qdrant
- Интеграция с камерами с системой прав доступа
- Очередь запросов на Redis с отображением статуса в реальном времени
- Полная поддержка i18n (RU/EN) через Flask-Babel
- Тёмная/светлая тема с сохранением предпочтений
- Экспорт чатов в HTML с встроенными медиафайлами

### 🔄 В работе
- Долговременная память диалогов (сохранение контекста между сеансами)
- Продвинутые функции RAG: фильтрация по метаданным, гибридный поиск, ре-ранжирование
- Оптимизация интерфейса для мобильных устройств
- Улучшение производительности
- Повышение безопасности
- Аналитика активности пользователей и статистика использования

### 📅 Запланировано
- Архитектура плагинов для пользовательских модулей
- API для внешних интеграций (вебхуки, REST-эндпоинты)
- Утилиты резервного копирования/восстановления пользовательских данных
- Функции совместной работы (общие сеансы, библиотеки документов)
- Утилиты локальной донастройки моделей (поддержка LoRA, QLoRA)

---

## 🤝 Участие в разработке
Мы приветствуем вклад в проект! Вот как вы можете помочь:
- 🔍 Сообщить об ошибке: нашли баг? Создайте issue с шагами воспроизведения
- 💡 Предложить функцию: есть идея? Начните обсуждение перед написанием кода
- 🛠️ Отправить PR: форкните, создайте ветку, напишите код, протестируйте, отправьте pull request
- 📚 Улучшить документацию: помогите уточнить документацию, примеры или переводы

---

## 📄 Лицензия
Этот проект распространяется под лицензией MIT – подробности см. в файле [LICENSE-ru](LICENSE-ru).

<br> <div align="center"> Сделано с ❤️ для сообщества локального ИИ </div>
  
