FROM python:3.9-slim

WORKDIR /app

# Устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY . .

# Создаем пользователя для запуска приложения
RUN addgroup --system --gid 1000 appuser && \
    adduser --system --uid 1000 --gid 1000 appuser

# Создаем папку для данных и даем права
RUN mkdir -p /app/data && \
    chown -R appuser:appuser /app && \
    chmod -R 755 /app/data

# Переключаемся на непривилегированного пользователя
USER appuser

# Запускаем приложение
CMD ["python", "wsgi.py"]