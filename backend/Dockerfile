FROM python:3.11-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ffmpeg libsm6 libxext6 libmagic1 \
    && rm -rf /var/lib/apt/lists/* && apt-get clean

WORKDIR /app

# Копирование зависимостей и установка
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование backend
COPY backend/ ./backend/

# Копирование frontend
COPY frontend/ ./frontend/

# Создание директорий для хранения
RUN mkdir -p storage/{uploads,sessions,cache} && \
    chmod 755 storage/{uploads,sessions,cache}

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]