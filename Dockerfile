FROM python:3.9-slim

WORKDIR /app

# Installing system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libjpeg-dev \
    zlib1g-dev \
    libtiff-dev \
    libwebp-dev \
    libmagic1 \
    libpq-dev \
    docker-cli \
    && rm -rf /var/lib/apt/lists/*

# Installing Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install PostgreSQL driver (optional)
RUN pip install --no-cache-dir psycopg2-binary==2.9.9 || echo "Warning: psycopg2 failed to install"

# Copying the code
COPY . .

# Compile translations
RUN pybabel compile -d translations

# Creating a user and a folder for the data
RUN addgroup --system --gid 1000 appuser && \
    adduser --system --uid 1000 --gid 1000 appuser && \
    mkdir -p /app/data && \
    chown -R appuser:appuser /app && \
    chmod -R 755 /app/data

# Switching to an unprivileged user
USER appuser

# Launching with Gunicorn (configuration from gunicorn_config.py)
CMD ["gunicorn", "-c", "gunicorn_config.py", "wsgi:app"]