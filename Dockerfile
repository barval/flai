FROM python:3.11-slim

WORKDIR /app

# Installing system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/postgresql-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql-archive-keyring.gpg] http://apt.postgresql.org/pub/repos/apt trixie-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    libjpeg-dev \
    zlib1g-dev \
    libtiff-dev \
    libwebp-dev \
    libmagic1 \
    libpq-dev \
    postgresql-client-18 \
    docker-cli \
    && rm -rf /var/lib/apt/lists/*

# Installing Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copying the code
COPY . .

RUN pybabel compile -d translations && \
    addgroup --system --gid 1000 appuser && \
    adduser --system --uid 1000 --gid 1000 appuser && \
    mkdir -p /app/data && \
    chown -R appuser:appuser /app && \
    chmod -R 755 /app/data

# Switching to an unprivileged user
USER appuser

# Launching with Gunicorn (configuration from gunicorn_config.py)
CMD ["gunicorn", "-c", "gunicorn_config.py", "wsgi:app"]