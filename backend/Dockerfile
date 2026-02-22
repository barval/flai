FROM python:3.11-slim

# Системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ffmpeg libsm6 libxext6 libmagic1 \
    && rm -rf /var/lib/apt/lists/* && apt-get clean

WORKDIR /app

# Копирование зависимостей (пути относительно контекста ".")
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r ./backend/requirements.txt

# Копирование backend
COPY backend/ ./backend/

# Копирование frontend ← ДОБАВЛЕНО
COPY frontend/ ./frontend/

# Директории для хранения
RUN mkdir -p /app/storage/{uploads,sessions,cache} && \
    chmod 755 /app/storage/{uploads,sessions,cache}

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Обратите внимание: app находится в backend.app
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]