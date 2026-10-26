FROM python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e

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
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Installing Python dependencies from the lock file.
# requirements.txt is the editable manifest; requirements.lock is the exact set
# installed in the image (regenerate it after a deliberate bump).
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

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