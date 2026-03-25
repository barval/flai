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
- 💬 **Интеллектуальный чат** – умная маршрутизация запросов (быстрые модели для простых, мощные для сложных)
- 🧠 **Продвинутое рассуждение** – выделенная модель для вычислений, генерации кода, творчества
- 🔍 **Мультимодальный анализ** – загрузка изображений и вопросы по их содержанию
- 🎨 **Генерация изображений** – создание картинок по тексту через Stable Diffusion с оптимизацией промптов
- 🎤 **Распознавание речи** – преобразование голосовых сообщений в текст через Whisper ASR
- 🗣️ **Синтез речи** – озвучивание ответов через Piper TTS (мужской/женский голос)

### 📁 Управление документами и знаниями
- 📚 **RAG с Qdrant** – загрузка документов (PDF, DOC, DOCX, TXT) и вопросы по содержимому
- 🗂️ **Сеансы чата** – множество независимых диалогов с авто-озаглавливанием
- 💾 **Экспорт чатов** – сохранение диалогов в HTML-файлы с встроенными медиа

### 🏠 Интеграция с домом (опционально)
- 📹 **Видеонаблюдение** – запрос снимков с IP-камер и анализ через мультимодальные модели
- 🔐 **Контроль доступа** – гранулярные права доступа к камерам для каждого пользователя

### 🔒 Конфиденциальность и безопасность
- 🏠 **100% локально** – вся обработка на вашем оборудовании
- 🔐 **Аутентификация по сессиям** – безопасный вход с хешированием паролей
- 🛡️ **Контроль доступа к файлам** – файлы доступны только авторизованным пользователям
- 🧹 **Изоляция данных** – данные каждого пользователя строго разделены

### 👥 Пользовательский опыт
- 🌐 **Мультиязычность** – полный интерфейс и ответы ИИ на русском и английском языках
- 🌓 **Тёмная/светлая тема** – переключение тем с сохранением предпочтений
- 🎚️ **Выбор голоса** – мужской или женский голос для ответов через TTS
- 📊 **Очередь запросов** – отслеживание статуса в реальном времени с индикацией позиции в очереди
- 📎 **Вложения файлов** – поддержка изображений, аудиофайлов и документов в диалогах
- 🔔 **Уведомления** – индикаторы непрочитанных сообщений и мигающие иконки для запросов в обработке/очереди

### ⚙️ Администрирование
- 👤 **Управление пользователями** – добавление, редактирование, удаление пользователей; смена паролей; назначение классов обслуживания
- 🔑 **Права на камеры** – контроль доступа пользователей к конкретным камерам (опционально)
- 🤖 **Управление моделями** – выбор и настройка моделей для чата, рассуждений, мультимодальных задач и эмбеддингов прямо из админ‑панели
- 📈 **Мониторинг системы** – просмотр размеров баз данных и системной статистики
- 🔧 **CLI-инструменты** – управление паролем администратора через Flask CLI

---

## 🏗️ Архитектура

ПЛИИ — модульное веб-приложение на Flask, координирующее несколько самостоятельно размещённых ИИ-сервисов.

### Основные компоненты

| Компонент | Назначение | Технология | Порт по умолчанию |
|-----------|------------|------------|-------------------|
| **Flask Web** | Веб-интерфейс, маршрутизация, API | Python | 5000 |
| **Ollama** | Инференс LLM (чат, рассуждения, мультимодальность) | Go + llama.cpp | 11434 |
| **Automatic1111** | Генерация изображений Stable Diffusion | Python + PyTorch | 7860 |
| **Whisper ASR** | Распознавание речи (транскрибация) | OpenAI Whisper | 9000 |
| **Piper TTS** | Синтез речи (текст в голос) | ONNX + Piper | 18888 |
| **Qdrant** | Векторная база данных для RAG | Rust | 6333 |
| **Redis** | Управление очередью запросов | C | 6379 |
| **SQLite** | Учётные записи, сеансы, сообщения | Встраиваемая СУБД | -- |

### Распределённое развёртывание

Каждый сервис может работать на отдельной машине для распределения нагрузки:
```text
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Web App   │────▶│   Ollama    │────▶│     GPU     │
│  (Flask)    │     │  (Узел 1)   │     │   Сервер    │
└─────────────┘     └─────────────┘     └─────────────┘
       │
       ▼
┌─────────────┐     ┌─────────────┐
│   Ollama    │────▶│     GPU     │
│  (Узел 2)   │     │   Сервер    │
└─────────────┘     └─────────────┘
```
Настройте отдельные URL Ollama для каждого типа моделей в Панели администратора (`/admin`).

---

## 📋 Системные требования

### Рекомендуемое оборудование
| Компонент | Минимум | Рекомендуется | с ГПУ |
|-----------|---------|---------------|-------|
| **ОЗУ** | 8 ГБ | 16–32 ГБ | 32 ГБ |
| **ЦПУ** | 4 ядра | 4+ ядер | 8+ ядер |
| **ГПУ** | Опционально | NVIDIA 8+ ГБ VRAM | NVIDIA 16+ ГБ VRAM |
| **Хранилище** | 20 ГБ | 60+ ГБ | 100+ ГБ |

### Программные требования
- Сервер с Linux (или Windows/macOS с Docker Desktop)
- Docker Engine ≥ 20.10
- Docker Compose ≥ 2.0
- Подключение к интернету (только для первоначальной загрузки моделей)
> 💡 **Примечание**: После загрузки моделей ПЛИИ работает полностью офлайн.

---

## 🚀 Быстрый запуск
Запустите ПЛИИ за несколько минут, выполнив следующие простые шаги:

### 1. Клонирование и базовая настройка
```bash
# Клонировать репозиторий
git clone https://github.com/barval/flai.git
cd flai

# Скопировать шаблон окружения
cp .env.example .env

# Сгенерировать безопасный секретный ключ
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")|" .env

# Отредактировать .env с вашими настройками (часовой пояс, URL API и т.д.)
nano .env
```

### 2. Подготовка дополнительных сервисов (Опционально, но рекомендуется)
> 💡 Примечание: Если вы хотите использовать генерацию изображений и голосовые функции, выполните шаги ниже. Если только чат — переходите к шагу 3.

#### 🎨 Для генерации изображений (Automatic1111):
```bash
# Создать папку для моделей
mkdir -p services/automatic1111/models

# Скачать чекпоинт Stable Diffusion (пример: CyberRealistic)
# Замените ссылку на нужную вам модель с civitai.com или huggingface
wget -O services/automatic1111/models/cyberrealisticXL_v90.safetensors \
https://huggingface.co/cyberreal/cyberRealisticXL/resolve/main/cyberrealisticXL_v90.safetensors

# В файле .env убедитесь, что указаны:
# AUTOMATIC1111_URL=http://flai-sd:7860
# AUTOMATIC1111_MODEL=cyberrealisticXL_v90.safetensors
```

#### 🎤 Для голосовых функций (Piper TTS + Whisper):
```bash
# Создать папку для голосовых моделей
mkdir -p services/piper/piper_models

# Скачать русские голоса (мужской и женский)
curl -L -o services/piper/piper_models/ru_RU-dmitri-medium.onnx \
https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx
curl -L -o services/piper/piper_models/ru_RU-dmitri-medium.onnx.json \
https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json

curl -L -o services/piper/piper_models/ru_RU-irina-medium.onnx \
https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx
curl -L -o services/piper/piper_models/ru_RU-irina-medium.onnx.json \
https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json

# В файле .env убедитесь, что указаны:
# PIPER_URL=http://flai-piper:8888/tts
# WHISPER_API_URL=http://flai-whisper:9000/asr
```

### 3. Запуск сервисов
Выберите вариант в зависимости от нужных функций:
```bash
# Вариант А: Полный функционал (Чат + Изображения + Голос + RAG)
docker-compose -f docker-compose.all.yml --profile with-image-gen --profile with-voice --profile with-rag up -d

# Вариант Б: Только чат и рассуждения (без изображений и голоса)
docker-compose -f docker-compose.all.yml up -d

# Вариант В: Чат + Голос (без генерации изображений)
docker-compose -f docker-compose.all.yml --profile with-voice up -d
```

### 4. Загрузка AI-моделей (Ollama)
```bash
# Дождаться запуска Ollama (около 30 секунд)
sleep 30

# Загрузить модели для чата, зрения, рассуждений и поиска
docker exec flai-ollama ollama pull qwen3:4b-instruct-2507-q4_K_M
docker exec flai-ollama ollama pull qwen3-vl:8b-instruct-q4_K_M
docker exec flai-ollama ollama pull gpt-oss:20b
docker exec flai-ollama ollama pull bge-m3:latest
```

### 5. Установка пароля администратора
```bash
# Создать пользователя admin с паролем
docker exec flai-web flask admin-password ВашБезопасныйПароль123
```

### 6. Доступ к приложению
Откройте браузер и перейдите по адресу: <http://localhost:5000>

Войдите с учётными данными:
- Логин: `admin`
- Пароль: (который вы установили в шаге 5)

### 7. Настройка моделей (Первый вход)
1. Перейдите в **Панель администратора** → вкладка **Модели**.
2. Для каждого модуля (Чат, Рассуждения, Мультимодальность, Эмбеддинги):
    + Нажмите 🔄 **Обновить** для загрузки доступных моделей.
    + Выберите загруженную модель из списка.
    + Нажмите **Сохранить**.
3. Для генерации изображений: Убедитесь, что в настройке модели выбран чекпоинт, который вы скачали в шаге 2.
4. Для голоса: Убедитесь, что в `.env` указан правильный путь к сервису Piper.

### ✅ Готово!
Теперь вы можете:
- 💬 Вести диалоги с ИИ
- 🎨 Генерировать изображения (если настроен Automatic1111)
- 🎤 Отправлять голосовые сообщения и слушать ответы (если настроен Piper/Whisper)
- 📚 Загружать документы для поиска (если включен профиль RAG)

---

## 🔧 Конфигурация

### Docker Compose «всё в одном»
Для запуска всех сервисов на одной машине используйте `docker-compose.all.yml`:
```yaml
# docker-compose.all.yml
version: '3.8'

services:
  # ============================================================
  # ВЕБ-ПРИЛОЖЕНИЕ (Обязательно)
  # ============================================================
  web:
    build: .
    container_name: flai-web
    ports:
      - "5000:5000"
    depends_on:
      - redis
    volumes:
      - .//app/data
      - ./.env:/app/.env:ro
    env_file:
      - .env
    environment:
      - REDIS_URL=redis://redis:6379/0
      # URL Ollama для каждого типа моделей (для распределённого развёртывания)
      - OLLAMA_CHAT_URL=http://ollama:11434
      - OLLAMA_REASONING_URL=http://ollama:11434
      - OLLAMA_MULTIMODAL_URL=http://ollama:11434
      - OLLAMA_EMBEDDING_URL=http://ollama:11434
    # Поддержка GPU: Раскомментируйте для NVIDIA GPU
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: 1
    #           capabilities: [gpu]
    networks:
      - flai_network
    restart: unless-stopped

  # ============================================================
  # REDIS (Обязательно - Очередь запросов)
  # ============================================================
  redis:
    image: redis:8.0.6-alpine
    container_name: flai-redis
    ports:
      - "6379:6379"
    volumes:
      - redis-/data
    command: redis-server --appendonly yes
    networks:
      - flai_network
    restart: unless-stopped
    # Отключить при использовании внешнего Redis:
    # Закомментируйте весь блок этого сервиса

  # ============================================================
  # OLLAMA (Обязательно - Инференс LLM)
  # ============================================================
  ollama:
    image: ollama/ollama:latest
    container_name: flai-ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama:/root/.ollama
    environment:
      - OLLAMA_REQUEST_TIMEOUT=1200s
      - OLLAMA_MAX_LOADED_MODELS=1
      - OLLAMA_KEEP_ALIVE=0
    # Поддержка GPU: Раскомментируйте для NVIDIA GPU
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: 1
    #           capabilities: [gpu]
    networks:
      - flai_network
    restart: unless-stopped
    # Отключить при использовании внешнего Ollama:
    # Закомментируйте весь блок этого сервиса

  # ============================================================
  # AUTOMATIC1111 (Опционально - Генерация изображений)
  # ============================================================
  automatic1111:
    image: siutin/stable-diffusion-webui-docker:latest-cuda
    container_name: flai-sd
    ports:
      - "7860:7860"
    volumes:
      - ./services/automatic1111/models:/app/stable-diffusion-webui/models
      - ./services/automatic1111/outputs:/app/stable-diffusion-webui/outputs
    environment:
      - NVIDIA_VISIBLE_DEVICES=all
      - NVIDIA_DRIVER_CAPABILITIES=compute,utility
      - NVIDIA_REQUIRE_CUDA=cuda>=12.1
    # Поддержка GPU: Требуется для разумной производительности
    # runtime: nvidia
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: 1
    #           capabilities: [gpu]
    networks:
      - flai_network
    restart: unless-stopped
    profiles:
      - with-image-gen
    # Отключить если не используется генерация изображений:
    # Закомментируйте весь блок этого сервиса ИЛИ используйте profiles

  # ============================================================
  # WHISPER ASR (Опционально - Распознавание речи)
  # ============================================================
  whisper:
    image: onerahmet/openai-whisper-asr-webservice:latest
    container_name: flai-whisper
    ports:
      - "9000:9000"
    environment:
      - ASR_MODEL=medium
      - ASR_ENGINE=faster_whisper
      - ASR_DEVICE=cpu
    # Поддержка GPU: Раскомментируйте для ускорения транскрибации
    # environment:
    #   - ASR_DEVICE=cuda
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: 1
    #           capabilities: [gpu]
    volumes:
      - ~/.cache/huggingface:/root/.cache/huggingface
    networks:
      - flai_network
    restart: unless-stopped
    profiles:
      - with-voice
    # Отключить если не используются голосовые функции:
    # Закомментируйте весь блок этого сервиса

  # ============================================================
  # PIPER TTS (Опционально - Синтез речи)
  # ============================================================
  piper:
    build:
      context: ./services/piper
      dockerfile: Dockerfile.piper
    container_name: flai-piper
    ports:
      - "18888:8888"
    volumes:
      - ./services/piper/piper_models:/app/models
    environment:
      - PIPER_MODEL_DIR=/app/models
    # Поддержка GPU: Не требуется (только CPU)
    networks:
      - flai_network
    restart: unless-stopped
    profiles:
      - with-voice
    # Отключить если не используются голосовые функции:
    # Закомментируйте весь блок этого сервиса

  # ============================================================
  # QDRANT (Опционально - Векторная база данных для RAG)
  # ============================================================
  qdrant:
    image: qdrant/qdrant:latest
    container_name: flai-qdrant
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - qdrant-/qdrant/storage
    environment:
      - QDRANT__SERVICE__API_KEY=${QDRANT_API_KEY:-}
      - QDRANT__SERVICE__ENABLE_TLS=0
    # Поддержка GPU: Обычно не требуется
    networks:
      - flai_network
    restart: unless-stopped
    profiles:
      - with-rag
    # Отключить если не используется поиск по документам:
    # Закомментируйте весь блок этого сервиса

networks:
  flai_network:
    driver: bridge

volumes:
  redis-
  ollama:
  qdrant-
```

### Примеры использования
```bash
# Запустить все сервисы
docker-compose -f docker-compose.all.yml up -d

# Запустить без генерации изображений (экономия памяти GPU)
docker-compose -f docker-compose.all.yml --profile with-voice --profile with-rag up -d

# Запустить со всеми функциями
docker-compose -f docker-compose.all.yml --profile with-image-gen --profile with-voice --profile with-rag up -d

# Остановить все сервисы
docker-compose -f docker-compose.all.yml down

# Просмотр логов
docker-compose -f docker-compose.all.yml logs -f web
```

### Распределённое развёртывание (несколько машин)
Для распределения нагрузки по нескольким узлам Ollama:

1. Машина 1 (Web + Чат-модели):
```bash
# .env на Машине 1
OLLAMA_CHAT_URL=http://machine1:11434
OLLAMA_REASONING_URL=http://machine2:11434
OLLAMA_MULTIMODAL_URL=http://machine3:11434
OLLAMA_EMBEDDING_URL=http://machine1:11434
```
2. Машина 2 (Модели рассуждений):
```bash
# Запустить только Ollama
docker-compose -f services/ollama/docker-compose.yml up -d
```
3. Машина 3 (Мультимодальные модели):
```bash
# Запустить только Ollama
docker-compose -f services/ollama/docker-compose.yml up -d
```
Настройте URL моделей в Панели администратора → вкладка Модели после первого входа.

---

## 🤖 Настройка моделей

### Необходимые модели (загрузить после запуска Ollama)
```bash
# Чат/Маршрутизатор (быстрые ответы)
docker exec flai-ollama ollama pull qwen3:4b-instruct-2507-q4_K_M

# Мультимодальная модель (анализ изображений)
docker exec flai-ollama ollama pull qwen3-vl:8b-instruct-q4_K_M

# Модель рассуждений (сложные задачи)
docker exec flai-ollama ollama pull gpt-oss:20b

# Модель эмбеддингов (поиск по документам RAG)
docker exec flai-ollama ollama pull bge-m3:latest
```

### Настройка моделей в Панели администратора
1. Войдите как администратор и перейдите в `/admin` → вкладка **Модели**
2. Для каждого модуля (Чат, Рассуждения, Мультимодальность, Эмбеддинги):  
  #### **Шаг 1: Укажите URL Ollama**  
   - Отметьте чек-бокс "Локально", если Ollama запущен на той же машине (URL автоматически заполняется `http://ollama:11434`)
   - Снимите галочку "Локально" и введите пользовательский URL для распределённого развёртывания (например, `http://192.168.1.50:11434`)
   - Иконка статуса показывает доступность (✅ доступна / ❌ недоступна)  
  #### **Шаг 2: Обновите список моделей**  
   - Нажмите кнопку 🔄 Обновить для получения списка моделей из Ollama
   - Дождитесь заполнения выпадающего списка названиями моделей  
  #### **Шаг 3: Выберите модель и настройте параметры**  
   - Выберите нужную модель из выпадающего списка
   - Ниже отобразится информация о модели (архитектура, параметры, длина контекста)
   - Настройте параметры:
      * **Длина контекста**: Максимальное количество токенов (должно быть ≤ максимума модели)
      * **Температура**: Креативность (0.0–2.0, меньше = более детерминировано)
      * **Top P**: Nucleus sampling (0.0–1.0)
      * **Таймаут**: Таймаут запроса в секундах (0–1200)
   - Нажмите Сохранить для применения конфигурации
> 💡 Смена модели эмбеддингов автоматически запускает переиндексацию всех документов.

---

## 🎨 Настройка генерации изображений

### 1. Скачать чекпоинт Stable Diffusion
```bash
# Создать директорию моделей
mkdir -p services/automatic1111/models

# Скачать чекпоинт (пример: CyberRealistic)
# Посетите https://civitai.com/ и скачайте предпочтительную модель
# Поместите файл .safetensors в services/automatic1111/models/
```

### 2. Настроить в `.env`
```bash
AUTOMATIC1111_URL=http://flai-sd:7860
AUTOMATIC1111_MODEL=cyberrealisticXL_v90.safetensors
AUTOMATIC1111_TIMEOUT=180
```

### 3. Включить в Docker Compose
Раскомментируйте сервис `automatic1111` или используйте profiles:
```bash
docker-compose -f docker-compose.all.yml --profile with-image-gen up -d
```

---

## 🎤 Настройка голосовых функций

### 1. Скачать голосовые модели
```bash
mkdir -p services/piper/piper_models

# Русский мужской голос
curl -L -o services/piper/piper_models/ru_RU-dmitri-medium.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx

curl -L -o services/piper/piper_models/ru_RU-dmitri-medium.onnx.json \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json

# Русский женский голос
curl -L -o services/piper/piper_models/ru_RU-irina-medium.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx

curl -L -o services/piper/piper_models/ru_RU-irina-medium.onnx.json \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json
```

### 2. Включить в Docker Compose
```bash
docker-compose -f docker-compose.all.yml --profile with-voice up -d
```

---

## 📚 Настройка RAG (поиск по документам)

### 1. Настроить Qdrant в `.env`
```bash
QDRANT_URL=http://flai-qdrant:6333
QDRANT_API_KEY=ваш_надёжный_api_ключ
EMBEDDING_MODEL=bge-m3:latest
RAG_CHUNK_SIZE=500
RAG_CHUNK_OVERLAP=50
RAG_TOP_K=5
```

### 2. Включить в Docker Compose
```bash
docker-compose -f docker-compose.all.yml --profile with-rag up -d
```

### 3. Загрузить документы
  1. Войдите в веб-интерфейс
  2. Нажмите вкладку Документы в боковой панели
  3. Нажмите ➕ для загрузки PDF, DOC, DOCX или TXT файлов
  4. Дождитесь завершения индексации (статус: ✅ Проиндексирован)

---

## 📹 Интеграция с камерами (опционально)
Модуль работы с камерами не включён в основной docker-compose и должен быть настроен отдельно.

### 1. Развёртывание сервиса камер
Сервис камер — это отдельный проект, предоставляющий снимки с IP-камер:
```bash
# Клонировать репозиторий API камер
git clone https://github.com/barval/room-snapshot-api.git
cd room-snapshot-api

# Настроить файл .env
cp .env.example .env
# Отредактировать .env с URL и учётными данными ваших камер

# Запустить сервис камер
docker-compose up -d
```

### 2. Настроить ПЛИИ для использования сервиса камер
В файле `.env` ПЛИИ:
```bash
# Включить модуль камер
CAMERA_ENABLED=true

# Адрес API камер (настройте IP/порт по необходимости)
CAMERA_API_URL=http://host.docker.internal:5005

# Таймаут запроса снимка (секунды)
CAMERA_API_TIMEOUT=15

# Интервал проверки доступности (секунды)
CAMERA_CHECK_INTERVAL=30
```

### 3. Настроить права доступа к камерам
  1. Войдите в ПЛИИ как администратор
  2. Перейдите в `/admin` → вкладка Пользователи
  3. Отредактируйте пользователя и отметьте камеры, к которым он имеет доступ:
    Например:
    - `tam` — тамбур  
    - `pri` — прихожая  
    - `kor` — коридор  
    - `spa` — спальня  
    - `kab` — кабинет  
    - `det` — детская  
    - `gos` — гостиная  
    - `kuh` — кухня  
    - `bal` — балкон  

### 4. Использование камер в чате
Пользователи с правами доступа могут спрашивать:
+ "Покажи кухню" → Возвращает снимок с камеры кухни
+ "Что в гостиной?" → Возвращает снимок + анализ ИИ
+ "Есть ли кто в кабинете?" → Возвращает снимок + анализ ИИ

---

## 👥 Управление пользователями

### Возможности Панели администратора
| Возможность | Описание |
|-------------|----------|
| 👤 Операции с пользователями | Создание, редактирование, удаление учётных записей |
| 🔑 Управление паролями | Сброс паролей для любого пользователя |
| 🔐 Права на камеры | Предоставление/отзыв доступа к камерам |
| 🤖 Управление моделями | Настройка моделей для каждого типа модуля |
| 📊 Системная статистика | Мониторинг размеров баз данных и хранилища |
| 🎚️ Классы обслуживания | Приоритет очереди (0=высший, 2=низший) |

### CLI-команды
```bash
# Установить пароль администратора
docker exec flai-web-1 flask admin-password НовыйПароль123

# Просмотр помощи
docker exec flai-web-1 flask --help
```

---

## 🧪 Нагрузочное тестирование
ПЛИИ включает скрипты нагрузочного тестирования на основе Locust.

### Настройка
```bash
# Создать виртуальное окружение
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

# Установить Locust
pip install locust
```

### Запуск тестов
```bash
# Веб-интерфейс
locust -f tests/load/locustfile.py --host http://localhost:5000

# Автоматический режим (headless)
locust -f tests/load/locustfile.py --host http://localhost:5000 \
  --headless -u 10 -r 2 --run-time 1m
```

### Тестовый пользователь
Создайте тестового пользователя перед запуском:
- Логин: `testuser`
- Пароль: `testpass`

> 💡 Обязательно: заблокируйте или удалите тестового пользователя после проведения тестов!

---

## 🗺️ Дорожная карта

### ✅ Завершено
- Маршрутизация запросов по моделям (простые → быстрая, сложные → рассуждения)
- Мультимодальный анализ изображений с историей диалога
- Генерация изображений с автоматической оптимизацией промптов
- Распознавание (Whisper) и синтез (Piper TTS) речи
- Загрузка документов + RAG с семантическим поиском через Qdrant
- Очередь запросов на Redis с отображением статуса в реальном времени
- Полная поддержка i18n (RU/EN) через Flask-Babel
- Тёмная/светлая тема с сохранением предпочтений
- Экспорт чатов в HTML с встроенными медиафайлами
- Админ-панель с управлением моделями
- Отображение статуса индексации документов с временем обработки
- Интеграция с камерами с системой прав доступа
- **Режим WAL для SQLite для лучшей конкурентности**
- **Нагрузочное тестирование с Locust**
- **Отдельные URL Ollama для каждого типа моделей (распределённое развёртывание)**

### 🔄 В работе
- Долговременная память диалогов (контекст между сеансами)
- Продвинутые функции RAG: фильтрация по метаданным, гибридный поиск, ре-ранжирование
- Оптимизация интерфейса для мобильных устройств
- Улучшение производительности
- Повышение безопасности
- Аналитика активности пользователей

### 📅 Запланировано
- Архитектура плагинов для пользовательских модулей
- API для внешних интеграций (вебхуки, REST)
- Утилиты резервного копирования/восстановления
- Функции совместной работы (общие сеансы, библиотеки документов)
- Локальная донастройка моделей (LoRA, QLoRA)

---

## 🤝 Участие в разработке
Мы приветствуем вклад в проект!
- 🔍 **Сообщить об ошибке**: Создайте issue с шагами воспроизведения
- 💡 **Предложить функцию**: Начните обсуждение перед написанием кода
- 🛠️ **Отправить PR**: Форкните, создайте ветку, напишите код, протестируйте, отправьте
- 📚 **Улучшить документацию**: Помогите уточнить документацию и переводы

---

## 📄 Лицензия
Этот проект распространяется под лицензией MIT – подробности см. в файле [LICENSE-ru](LICENSE-ru).

<br> <div align="center"> Сделано с ❤️ для сообщества локального ИИ </div>
