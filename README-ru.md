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
- 💬 Интеллектуальный чат – общайтесь с локальными LLM через Ollama (маршрутизация, рассуждения, мультимодальность).
- 🎨 Генерация изображений – создавайте картинки по текстовому описанию с помощью Stable Diffusion (Automatic1111).
- 🔍 Анализ изображений – загружайте фото и задавайте вопросы о них (мультимодальная модель).
- 🎤 Распознавание речи – преобразуйте голосовые сообщения в текст с помощью Whisper ASR.
- 📹 Домашнее видеонаблюдение – запрашивайте снимки с IP‑камер (опционально) и анализируйте их.
- 🗂️ Сеансы чата – множество независимых диалогов с авто‑озаглавливанием и индикаторами непрочитанного.
- ⚙️ Панель администратора – управляйте пользователями, назначайте права на камеры, меняйте пароли.
- 🚦 Очередь запросов – очередь на базе Redis с отображением статуса и позиции в реальном времени.
- 💾 Экспорт чатов – сохраняйте любой диалог в чистый HTML‑файл.
- 🔒 Полностью локально – всё работает на вашем оборудовании; данные никогда не покидают вашу сеть.

---

## 🧱 Архитектура
ПЛИИ — это веб‑приложение на Flask, которое оркестрирует несколько самостоятельно размещённых AI‑сервисов:
- **Ollama** – предоставляет чат‑модели, модели для рассуждений и мультимодальные модели.
- **Automatic1111** – WebUI Stable Diffusion для генерации изображений.
- **Whisper ASR** – сервис распознавания речи (faster‑whisper или OpenAI Whisper).
- **Redis** – управляет очередью запросов, чтобы веб‑интерфейс оставался отзывчивым.
- **SQLite** – хранит учётные записи пользователей, сеансы чата и сообщения.

Все компоненты могут работать в Docker‑контейнерах, что упрощает развёртывание.

---

## Требования
- Сервер с Linux (или Windows/macOS с Docker Desktop), на котором установлены Docker и Docker Compose.
- Не менее 8 ГБ ОЗУ (рекомендуется больше для больших моделей).
- NVIDIA GPU с поддержкой CUDA (для ускорения это желательно, но не обязательно).
- Подключение к интернету требуется только для загрузки моделей; после этого всё работает офлайн.

---

## 🚀 Быстрый старт
### 1. Клонируйте репозиторий
```bash
git clone https://github.com/barval/flai.git
cd flai
```
### 2. Подготовьте файл конфигурации
Скопируйте пример файла окружения:
```bash
cp .env.example .env
```
Отредактируйте файл `.env`, указав свои значения (см. `Конфигурация` ниже).
### 3. Запустите веб‑приложение ПЛИИ
```bash
docker-compose up -d
```
Приложение станет доступно по адресу `http://localhost:5000`.
### 4. Создайте пароль администратора
```bash
docker exec -it flai_web_1 flask admin-password <ваш_пароль_администратора>
```
Теперь вы можете войти с логином `admin` и установленным паролем.

---

## 🔧 Настройка зависимых сервисов
ПЛИИ полагается на внешние сервисы: Ollama, Automatic1111 и Whisper.
Вы можете запустить их на той же машине, используя примеры Docker Compose ниже.
Важно: Все сервисы должны быть подключены к одной Docker‑сети (например, flai_network), чтобы ПЛИИ мог обращаться к ним по именам контейнеров.

Сначала создайте общую сеть:
```bash
docker network create flai_network
```

### 🤖 Ollama (сервер LLM)
Создайте docker-compose.yml для Ollama:
```yaml
services:
  ollama:
    image: ollama/ollama
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
    # Раскомментируйте для поддержки GPU
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
Загрузите необходимые модели:
```bash
docker exec ollama ollama pull qwen3:4b-instruct-2507-q4_K_M
docker exec ollama ollama pull qwen3-vl:8b-instruct-q4_K_M
docker exec ollama ollama pull gpt-oss:20b
```

### 🎨 Automatic1111 (WebUI Stable Diffusion)
```yaml
services:
  automatic1111:
    # image: siutin/stable-diffusion-webui-docker:latest-cuda # CPU
    image: siutin/stable-diffusion-webui-docker:latest-cuda   # GPU
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
    # GPU
      - NVIDIA_VISIBLE_DEVICES=all
      - NVIDIA_DRIVER_CAPABILITIES=compute,utility
      - NVIDIA_REQUIRE_CUDA=cuda>=13.1
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
      - --opt-split-attention

networks:
  flai_network:
    external: true
```
Поместите ваш чекпоинт Stable Diffusion (например, `cyberrealisticXL_v90.safetensors`) в каталог `./models`.

### 🎤 Whisper ASR (распознавание речи)
```yaml
services:
  openai-whisper:
    image: onerahmet/openai-whisper-asr-webservice:latest         # CPU
    # image: onerahmet/openai-whisper-asr-webservice:latest-gpu   # GPU
    container_name: openai-whisper
    networks:
      - flai_network
    ports:
      - "9000:9000"
    environment:
      ASR_MODEL: "medium"                # или "small", "large"
      ASR_ENGINE: "faster_whisper"       # рекомендуется faster_whisper
      ASR_DEVICE: "cpu"                  # измените на "cuda" для GPU
    volumes:
      - ~/.cache/huggingface:/root/.cache/huggingface
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: always
    # Раскомментируйте для поддержки GPU
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: 1
    #           capabilities: [gpu]

networks:
  flai_network:
    external: true
```

---

## ⚙️ Конфигурация (.env)
Все настройки задаются в файле `.env`. Ниже приведены наиболее важные переменные; полный список см. в `.env.example`.

| Переменная | Описание	| Пример |
|------------|----------|--------|
| `SECRET_KEY` | Секрет для сессий Flask (сгенерируйте надёжный) | `mysecretkey` |
| `TIMEZONE` | Ваш локальный часовой пояс | `Europe/Moscow` |
| `OLLAMA_URL` | Адрес API Ollama | `http://ollama:11434` |
| `LLM_CHAT_MODEL` | Модель‑маршрутизатор / чат‑модель | `qwen3:4b-instruct-2507-q4_K_M` |
| `LLM_MULTIMODAL_MODEL` | Мультимодальная модель для изображений | `qwen3-vl:8b-instruct-q4_K_M` |
| `LLM_REASONING_MODEL` | Модель для сложных рассуждений | `gpt-oss:20b` |
| `AUTOMATIC1111_URL` | Адрес API Automatic1111	| `http://sd-webui:7860` |
| `AUTOMATIC1111_MODEL` | Имя чекпоинта Stable Diffusion | `cyberrealisticXL_v90.safetensors` |
| `WHISPER_API_URL` | Адрес API Whisper ASR | `http://openai-whisper:9000/asr` |
| `CAMERA_API_URL` | Адрес API камер (если используется) | `http://host.docker.internal:5005` |
| `FOOTER_TEXT` | Пользовательский текст подвала | `ПЛИИ v6.0 (с) 2026` |

---

## 👥 Управление пользователями
Вы можете управлять пользователями через Панель администратора (/admin) – добавлять, редактировать, удалять, менять пароли и назначать права доступа к камерам.

Пароль для учетной записи администратора создается и изменяется с помощью команды:
```bash
docker exec -it flai_web_1 flask admin-password <ваш_пароль_администратора>
```

---

## 🗺️ Планы развития
- 🗣️ Синтез речи (TTS) – озвучивание ответов ассистента с помощью локального TTS‑движка (например, Coqui TTS, Piper) для полноценного голосового взаимодействия.
- 📚 RAG с Qdrant – реализация генерации с дополнением извлечения (Retrieval‑Augmented Generation) по загруженным пользователем документам (PDF, TXT и др.) с использованием векторной базы данных Qdrant для семантического поиска.
- 🧠 Долговременная память диалогов – поддержание длительного контекста между сеансами путём суммаризации или хранения истории общения.

---

## 📄 Лицензия
Этот проект распространяется под лицензией MIT – подробности см. в файле [LICENSE-ru](LICENSE-ru).

<br> <div align="center"> Сделано с ❤️ для сообщества локального ИИ </div>
  
