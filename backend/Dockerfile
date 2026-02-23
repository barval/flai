FROM python:3.11-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    libsm6 \
    libxext6 \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Рабочая директория
WORKDIR /app

# Копирование зависимостей (пути относительно контекста ".")
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r ./backend/requirements.txt

# Копирование приложения
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Создание директорий для хранения
RUN mkdir -p /app/storage/{uploads,sessions,cache} && \
    chmod 755 /app/storage/{uploads,sessions,cache}

# Переменные окружения для Python
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app:/app/backend

# Порт приложения
EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Запуск приложения (путь с учётом структуры backend/)
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]