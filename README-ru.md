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
- 🔐 **Аутентификация по сессиям** – безопасный вход с хешированием паролей (Werkzeug)
- 🛡️ **Контроль доступа к файлам** – файлы доступны только авторизованным пользователям
- 🧹 **Изоляция данных** – данные каждого пользователя строго разделены
- 🔑 **CSRF-защита** – защита от подделки межсайтовых запросов для всех форм
- 🚦 **Rate Limiting** – защита от перебора паролей (5 попыток/минуту)
- 🔒 **Безопасность сессий** – HttpOnly и SameSite cookies, secure flag для HTTPS
- 📝 **Audit Logging** – логирование попыток входа и действий администратора
- 🔐 **HMAC-подпись очереди** – задачи Redis очереди подписаны для защиты от подделки

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

Каждый сервис может работать на отдельной машине для распределения нагрузки. См. [services/README.md](services/README.md) для подробных инструкций.

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

**Варианты развёртывания сервисов:**
- **Локальное**: Запуск на том же сервере что и FLAI (внутренняя сеть Docker)
- **Удалённое**: Запуск на отдельном сервере (требует настройки firewall)

Настройте отдельные URL Ollama для каждого типа моделей в Панели администратора (`/admin`).

---

## 📋 Системные требования

### Рекомендуемое оборудование
| Компонент | Минимум | Рекомендуется | Оптимально |
|-----------|---------|---------------|------------|
| **ОЗУ** | 8 ГБ | 16–32 ГБ | 32+ ГБ |
| **ЦПУ** | 4 ядра | 4+ ядер | 8+ ядер |
| **ГПУ** | NVIDIA 8-12 ГБ VRAM | NVIDIA 16 ГБ VRAM | NVIDIA 16+ ГБ VRAM |
| **Хранилище** | 40 ГБ | 60+ ГБ SSD | 100+ ГБ SSD NVMe |

### Программные требования
- Сервер с Linux (или Windows/macOS с Docker Desktop)
- Docker Engine ≥ 20.10
- Docker Compose ≥ 2.0
- Подключение к интернету (только для первоначальной загрузки моделей)
> 💡 **Примечание**: После загрузки моделей ПЛИИ работает полностью офлайн.

---

## 🚀 Быстрый запуск
Запустите ПЛИИ за несколько минут, выполнив следующие простые шаги:
> 💡 **Примечание**: У Вас должны быть установлены **драйвера NVIDIA** на хост-машине и **NVIDIA Container Toolkit**.

### 1. Клонирование и базовая настройка
```bash
# Клонировать репозиторий
git clone https://github.com/barval/flai.git
cd flai

# Создать папки и указать владельца
sudo mkdir -p data \
              data/uploads \
              data/documents
sudo chown -R 1000:1000 data

# Скопировать шаблон окружения
cp .env.example .env

# Сгенерировать безопасный секретный ключ
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")|" .env

# Сгенерировать API-ключ для Qdrant
sed -i "s|^QDRANT_API_KEY=.*|QDRANT_API_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")|" .env

# Отредактировать .env с вашими настройками (часовой пояс, URL API и т.д.)
nano .env
```

### 2. Подготовка дополнительных сервисов (Опционально, но рекомендуется)
> 💡 Примечание: Если вы хотите использовать генерацию изображений и голосовые функции, выполните шаги ниже. Если только чат — переходите к шагу 3.

#### 🎨 Для генерации изображений (Automatic1111):
```bash
# Создать папку для моделей
sudo mkdir -p services/automatic1111/models \
              services/automatic1111/models/Stable-diffusion \
              services/automatic1111/outputs
sudo chown -R 1000:1000 services

# Скачать чекпоинт Stable Diffusion (пример: RealVisXL_V4.0)
# Замените ссылку на нужную вам модель с civitai.com или huggingface
wget -O services/automatic1111/models/Stable-diffusion/RealVisXL_V4.0.safetensors \
  "https://huggingface.co/SG161222/RealVisXL_V4.0/resolve/main/RealVisXL_V4.0.safetensors"

# В файле .env убедитесь, что указаны:
# AUTOMATIC1111_URL=http://flai-sd:7860
# AUTOMATIC1111_MODEL=RealVisXL_V4.0.safetensors
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

# Скачать английские голоса (мужской и женский)
curl -L -o services/piper/piper_models/en_US-ryan-medium.onnx \
https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx  
curl -L -o services/piper/piper_models/en_US-ryan-medium.onnx.json \
https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json  

curl -L -o services/piper/piper_models/en_US-ljspeech-medium.onnx \
https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx  
curl -L -o services/piper/piper_models/en_US-ljspeech-medium.onnx.json \
https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx.json

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

### Переменные окружения (.env)

**Обязательные:**
```bash
SECRET_KEY=your_secret_key_here      # Секрет Flask для сессий
TIMEZONE=Europe/Moscow              # Ваш часовой пояс
```

**URL сервисов:**
```bash
OLLAMA_URL=http://flai-ollama:11434
AUTOMATIC1111_URL=http://flai-sd:7860
WHISPER_API_URL=http://flai-whisper:9000/asr
PIPER_URL=http://flai-piper:8888/tts
QDRANT_URL=http://flai-qdrant:6333
QDRANT_API_KEY=your_qdrant_api_key
CAMERA_API_URL=http://flai-room-snapshot-api:5005
```

**Повторные попытки подключения:**
```bash
SERVICE_RETRY_ATTEMPTS=15           # Количество попыток
SERVICE_RETRY_DELAY=2               # Задержка между попытками (сек)
```

**Безопасность сессий:**
```bash
HTTPS_ENABLED=true                  # true для HTTPS прокси
PERMANENT_SESSION_LIFETIME=28800    # Время жизни сессии (8 часов)
```

**Redis очередь:**
```bash
REDIS_RESULT_TTL=3600              # TTL результатов (1 час)
QUEUE_MAX_WAIT_TIME=300            # Макс. ожидание в очереди (5 мин)
```

### Конфигурация Docker

**Настройки Gunicorn (Dockerfile):**
```dockerfile
# Оптимизировано для I/O операций (ожидание ответов AI)
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--threads", "4", \
     "--worker-class", "gthread", \
     "--timeout", "120", \
     "--keep-alive", "5", \
     "wsgi:app"]
```

**Почему 1 worker × 4 threads?**
- Минимальное потребление RAM (+40МБ vs 1/1)
- Обрабатывает 4 одновременных подключения
- Оптимально для I/O bound (ожидание Ollama/SD)
- Экономия 280МБ vs 4 workers

### Docker Compose «всё в одном»
Для запуска всех сервисов на одной машине используйте `docker-compose.all.yml`:

### Примеры использования
```bash
# Запустить все сервисы
docker-compose -f docker-compose.all.yml up -d

# Запустить без генерации изображений
docker-compose -f docker-compose.all.yml --profile with-voice --profile with-rag up -d

# Запустить со всеми функциями
docker-compose -f docker-compose.all.yml --profile with-image-gen --profile with-voice --profile with-rag up -d

# Остановить все сервисы
docker-compose -f docker-compose.all.yml down

# Просмотр логов
docker-compose -f docker-compose.all.yml logs -f web
```

### Распределённое развёртывание (несколько машин)

Для распределения нагрузки между несколькими серверами используйте автономные docker-compose файлы в директории `services/`:

1. **Web App + Redis** (Сервер 1):
```bash
docker-compose -f docker-compose.all.yml up -d web redis
```

2. **Ollama - Chat Models** (Сервер 2):
```bash
cd services/ollama
docker-compose -f docker-compose.gpu.yml up -d
```

3. **Ollama - Reasoning Models** (Сервер 3):
```bash
cd services/ollama
docker-compose -f docker-compose.gpu.yml up -d
```

4. **Настройте URL моделей** в Панели администратора → вкладка Models:
```
Chat: http://server2:11434
Reasoning: http://server3:11434
Multimodal: http://server4:11434
Embedding: http://server2:11434
```

**Настройка Firewall:**
```bash
# На каждом удалённом сервере
sudo ufw allow from <web-app-ip> to any port <service-port>
```

См. [services/README.md](services/README.md) для полных инструкций по развёртыванию каждого сервиса.

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

# Скачать чекпоинт Stable Diffusion (пример: RealVisXL_V4.0)
# Замените ссылку на нужную вам модель с civitai.com или huggingface
wget -O services/automatic1111/models/RealVisXL_V4.0.safetensors \
  "https://huggingface.co/SG161222/RealVisXL_V4.0/resolve/main/RealVisXL_V4.0.safetensors"
```

### 2. Настроить в `.env`
```bash
AUTOMATIC1111_URL=http://flai-sd:7860
AUTOMATIC1111_MODEL=RealVisXL_V4.0.safetensors
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

Сервис камер — отдельный проект. Доступны два варианта развёртывания:

**Вариант A: Локальное развёртывание (на том же сервере что и ПЛИИ)**
```bash
cd services/room-snapshot-api
./deploy.sh local
```

**Вариант B: Удалённое развёртывание (на отдельном сервере)**
```bash
# Клонировать репозиторий API камер
git clone https://github.com/barval/room-snapshot-api.git
cd room-snapshot-api

# Настроить файл .env
cp .env.example .env
# Отредактировать .env с URL и учётными данными ваших камер

# Развернуть удалённо
./deploy.sh remote

# Настроить firewall
sudo ufw allow from <flai-server-ip> to any port 5005
```

См. [services/room-snapshot-api/README.md](services/room-snapshot-api/README.md) для подробных инструкций.

### 2. Настроить ПЛИИ для использования сервиса камер
В файле `.env` ПЛИИ:
```bash
# Включить модуль камер
CAMERA_ENABLED=true

# Адрес API камер (настройте IP/порт по необходимости)
# Для локального развёртывания:
CAMERA_API_URL=http://flai-room-snapshot-api:5005
# Для удалённого развёртывания:
CAMERA_API_URL=http://<camera-server-ip>:5005

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
docker exec flai-web flask admin-password НовыйПароль123

# Просмотр помощи
docker exec flai-web flask --help
```

---

## 🔍 Мониторинг и здоровье

### Health Check Endpoint

Комплексная проверка здоровья всех сервисов:

```bash
curl http://localhost:5000/health
```

**Ответ:**
```json
{
  "status": "ok",
  "timestamp": "2026-04-02T00:34:08.237346",
  "services": {
    "web": "ok",
    "database": "ok",
    "redis": "ok",
    "ollama": "ok"
  }
}
```

**Значения статуса:**
- `ok` — все сервисы работают
- `degraded` — часть сервисов недоступна
- `error` — все сервисы недоступны

### Prometheus Metrics

Метрики в формате Prometheus:

```bash
curl http://localhost:5000/metrics
```

**Доступные метрики:**
- `flai_web_info` — Версия сервиса
- `flai_queue_length` — Длина очереди
- `flai_queue_processing` — Обрабатываемые задачи
- `flai_database_size_bytes` — Размер БД
- `flai_requests_total` — Счётчик запросов
- `flai_uptime_seconds` — Время работы

### API Документация

Полная API документация в формате OpenAPI:
- **Файл:** `docs/openapi.yaml`
- **Формат:** OpenAPI 3.0
- **Покрытие:** Все REST endpoint'ы

Просмотр через Swagger UI или любой OpenAPI-совместимый просмотрщик.

---

## 🧪 Тестирование

### Юнит и интеграционные тесты

FLAI включает комплексное покрытие тестами критичных компонентов:

```bash
# Запустить все тесты
pytest

# Запустить с отчётом покрытия
pytest --cov=app --cov=modules --cov-report=html

# Запустить конкретную категорию
pytest tests/test_admin_routes.py
pytest tests/test_documents_routes.py
pytest tests/test_image_module.py
```

**Покрытие тестами:**
- `test_admin_routes.py` — Endpoint'ы админ-панели (17 тестов)
- `test_documents_routes.py` — Загрузка документов/RAG (16 тестов)
- `test_image_module.py` — Генерация изображений (16 тестов)
- `test_queue.py` — Операции Redis очереди
- `test_audio_module.py` — Аудио транскрибация
- `test_security.py` — Функции безопасности (CSRF, rate limiting и др.)
- `test_integration.py` — Сквозные интеграционные тесты

### Нагрузочное тестирование

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

> 💡 **Обязательно:** заблокируйте или удалите тестового пользователя после проведения тестов!

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
- **Улучшения безопасности:**
  * CSRF-защита для всех форм
  * Rate limiting для login (защита от перебора паролей)
  * Валидация владения сессией
  * Защита от path traversal
  * HMAC-подпись задач Redis очереди
  * Заголовки безопасности (CSP, X-Frame-Options и др.)
  * Audit logging для событий безопасности
- **Автономное развёртывание сервисов:**
  * Ollama (с поддержкой GPU)
  * Automatic1111 (Stable Diffusion)
  * Whisper ASR
  * Piper TTS
  * Qdrant (векторная БД)
  * Room Snapshot API (локальное/удалённое развёртывание)

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
