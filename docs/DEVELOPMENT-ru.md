# Разработка

Руководство для контрибьюторов ПЛИИ: где лежит код, как запускать набор тестов и какие CLI-инструменты поставляются вместе с приложением. Правила проекта и жёсткие ограничения описаны в [../AGENTS.md](../AGENTS.md) — этот документ предполагает, что вы их уже прочитали.

## Расположение кода

| Путь | Что там находится |
|------|------------------|
| `app/__init__.py` | `create_app()` — точка входа Flask и регистрация блюпринтов |
| `app/routes/` | HTTP-слой: auth, chat, admin, queue, messages, sessions, documents, backups, events, debug, rlm, `/v1` |
| `app/queue.py` | `RedisRequestQueue` — быстрый (CPU) и медленный (GPU) воркеры, сериализация VRAM, обработчики задач |
| `app/resource_manager.py` | Стоимость KV-кэша по модулям, оценка VRAM, `ensure_vram_for()` |
| `app/llamacpp_client.py` | `DirectLlamaBackend` и `LlamaSwapBackend` |
| `app/database.py` | `get_db()`, инициализация схемы, `_autofit_context()` |
| `app/tasks/` | Фоновые задачи: `dry_load.py`, `health_monitor.py` |
| `modules/` | Подсистемы моделей: base/router, multimodal, sd_cpp, cam, rag, audio, tts, slm, search, video, crawler, rlm |
| `prompts/ru/`, `prompts/en/` | Шаблоны промптов, `skills.txt` (единственный источник правды для списков возможностей) |
| `services/` | Сервисы не на Python (kokoro TTS, обёртка ltxvideo) |
| `tests/` | Набор тестов pytest, `tests/load/` для Locust |
| `translations/` | Каталоги Flask-Babel (`en`, `ru`) |

## Линт и проверка типов

```bash
ruff check .
mypy app/ modules/               # CI runs with `|| true` — does not block
```

## Тесты

ПЛИИ поставляет тесты для каждого ключевого компонента, а также нагрузочные тесты для веб-интерфейса.

### Установка

```bash
pip install -e ".[test]"
```

### Запуск

```bash
pytest                           # everything
pytest -m unit                   # only unit tests (no external deps)
pytest -m "not slow"             # skip slow tests
pytest -m "not (requires_db or requires_redis)"
pytest --cov=app --cov=modules --cov-report=html
pytest tests/test_queue.py       # a specific file
```

Каждый тестовый модуль создаёт собственные Flask-приложения (`create_app()` → потоки воркеров, Redis, Qdrant), поэтому между модулями может протекать состояние. В CI весь набор запускается как `pytest --forked` (`pytest-forked` в dev-экстрах): каждый модуль выполняется в собственном подпроцессе, что изолирует это состояние и проявляет ошибки отдельного модуля вместо межмодульных эффектов порядка. На одиночной машине разработки та же изоляция применима на более крупном уровне — запускайте только затронутые наборы (о бюджете памяти см. AGENTS.md), а сборка Docker-образа выполняется только при успешном прохождении набора.

ПЛИИ включает комплексное тестирование всех ключевых компонентов и нагрузочное тестирование веб-интерфейса.

### Нагрузочное тестирование

Нагрузочные тесты используют [Locust](https://locust.io/) для эмуляции одновременных пользователей.

```bash
# Install Locust (if not already installed)
pip install locust

# Web interface — open http://localhost:8089
locust -f tests/load/locustfile.py --host http://localhost:5000

# Headless mode — 10 users, spawn 2/sec, run 1 minute
locust -f tests/load/locustfile.py --headless -u 10 -r 2 --run-time 1m

# Using the convenience script
./tests/load/run_load_test.sh --host http://localhost:5000 --users 10 --spawn-rate 2 --run-time 1m
```

Подробные инструкции по нагрузочному тестированию — в [tests/load/README-ru.md](../tests/load/README-ru.md).

---

### Структура тестов

- **Фикстуры** в `tests/conftest.py`:
  - `test_app` — изолированное приложение + временные каталоги
  - `client` — тестовый клиент Flask
  - `runner` — раннер CLI

### Моки внешних сервисов

**Внешние сервисы ВСЕГДА заменяются моками**:
- Redis (`redis.from_url`)
- llama.cpp (`app.llamacpp_client.LlamaCppClient`)
- Qdrant (`modules.rag.QdrantClient`)

### Режим базы данных

- **Мок по умолчанию** (без `DATABASE_URL`).
- **В CI** (`DATABASE_URL` задан) — настоящий PostgreSQL с `TRUNCATE` между тестами в teardown фикстуры `test_app`.

### Фоновые воркеры

Потоки `RedisRequestQueue` останавливаются через `stop_workers(timeout=3)` в teardown фикстуры `test_app`, чтобы pytest не зависал.

### Доступные маркеры

- `unit` — быстрые юнит-тесты
- `integration` — тесты, требующие внешних сервисов (мокируются)
- `e2e` — сквозные тесты
- `slow` — долгие тесты
- `requires_db` — тесты, требующие PostgreSQL
- `requires_redis` — тесты, требующие Redis

### Примеры тестов

`tests/test_resource_manager_ltx_unload.py`

11 тестов в 4 классах:
  - `Preflight` — логика предварительной проверки
  - `Cache` — поведение кэша результатов на 30 с
  - `SuccessCondition` — достижимое условие успеха
  - `DockerRestart` — перезапуск Docker после трёх таймаутов подряд

`tests/test_morph.py` **(НОВОЕ в v9.0)**

16 тестов морфологического анализа pymorphy3 для названий комнат камер.

`tests/test_slm_rules.py` **(НОВОЕ в v9.0)**

29 тестов извлечения фактов SLM на основе правил: разбиение на предложения, оценка по паттернам категорий (предпочтения, факты, инструкции, особенности личности), нормализация текста, сходство по Левенштейну, извлечение фактов, дедупликация и разбор явного запроса «запомни».

`tests/test_slm_merge_rules.py` **(НОВОЕ в v9.0)**

16 тестов слияния фактов SLM на основе правил: fast_cleanup (точные дубликаты, фрагменты), edit_distance_merge (почти-дубликаты по Левенштейну), fragment_merge (более строгое определение подстрок), temporal_decay (автоархивирование старых фактов с низкой уверенностью) и логика планирования слияния.

`tests/test_backups.py`

Исправлено в v9.0:
в фикстуру `app` добавлен `Babel(flask_app)` (вызывало KeyError 'babel')
`test_restore_backup` исправлен через `dirs_exist_ok=True` в `app/routes/backups.py:restore_backup()` (вызывало `shutil.copytree FileExistsError` на `data/slm`)

`tests/test_api_docs.py` **(НОВОЕ в v12.3)**

5 тестов интерактивного справочника по API: Swagger UI отдаётся без ссылок на CDN (атрибуты `src`/`href` только у тегов `script`/`link`/`img` — обычные ссылки-атрибуции `<a href>` ничего не загружают), валидный документ OpenAPI 3.0.3 по адресу `/v1/openapi.json`, объявлена глобальная Bearer-аутентификация, **защита от расхождения маршрутов** (каждый зарегистрированный маршрут `/v1` обязан иметь запись в спеке и наоборот; маршруты документации `/v1/docs*` + `/v1/openapi.json` исключены) и наличие ключевых путей. Эта защита не даёт `docs/openapi-v1.yaml` отстать от реализации.

`tests/test_api_inventory.py` **(НОВОЕ в v12.3)**

Контрактный тест, перечисляющий все 23 ожидаемых эндпоинта `/v1` с их наборами методов; сборка падает при отсутствующем или неожиданном маршруте (маршруты документации `/v1/docs`, `/v1/docs/oauth2-redirect.html`, `/v1/openapi.json` вынесены в отдельный белый список).

Файлы тестов публичного API `/v1` (все добавлены в v12.3): `test_api_v1_auth.py` (Bearer-аутентификация, 8 тестов), `test_api_v1_chat.py` (чат sync/SSE/непрерывность сессии, 28 тестов), `test_api_v1_embeddings.py` (12 тестов), `test_api_v1_media.py` (генерация изображений/видео/аудио, 77 тестов), `test_api_v1_ratelimit.py` (9 тестов), `test_api_chat_async.py` (асинхронный чат + поллинг, 16 тестов), `test_api_tasks.py` (список/статус/отмена/содержимое задач с проверкой владельца, 27 тестов), `test_api_documents.py` (12 тестов), `test_api_rlm.py` (8 тестов), `test_api_sessions.py` (8 тестов), `test_api_limits.py` (19 тестов: лимит слотов ожидания, CORS), `test_api_bridge.py` (68 тестов: разрешение сессии, постановка в очередь, ожидание, стриминг, проверка владельца при перепостановке), `test_api_tokens.py` (управление API-ключами, 11 тестов).

**Файлы тестов веб-краулера (Crawl4AI, v12.4) (все новые в v12.4):** `test_crawler_guard.py` (8 тестов: диапазоны SSRF, credentials, порт), `test_crawler_config.py` (3 теста: значения allow-list по умолчанию, разбор env), `test_crawler_module.py` (15 тестов: доступность, контракт /md, область клиентского BFS, капы, дедлайн, антибот `SiteBlockedError`), `test_read_page_tool.py` (7 тестов: схема инструмента, гейтинг, локализованные ошибки), `test_router_crawl.py` (2 теста: разбор маркера `[-CRAWL-]`, маркер поиска не тронут), `test_crawl_task.py` (42 теста: выделенный исполнитель, отсутствие связки с GPU вне лока индексации, замена домена, квота, бюджет контекста, откат на веб-поиск при антиботе), `test_crawl_ui.py` (15 тестов: размещение сервиса в compose + отсутствие опубликованных портов, `docker compose config`, метки стадий в каталогах ru/en против msgid в chat.html, счётчик читает `data.pages`, инициализация флага деплоя).

### Известные проблемы с тестами
  - **Скорость юнит-тестов:** у `CamModule` есть 5 повторов инициализации по 2 с, из-за чего `test_cam.py` занимает ~10 с на фикстуру. Сборку не блокирует, но медленно.
  - **Нагрузочные тесты** (`tests/load/`) исключены из коллекции pytest — им нужны фикстуры locust. Запускаются отдельно: `locust -f tests/load/locustfile.py --host http://localhost:5000` или `locust -f tests/load/locustfile_public.py --host http://localhost:5000` для публичных эндпоинтов.

### Исправления тестовой инфраструктуры (v9.0)
  - `tests/test_backups.py`: добавлен `Babel(flask_app)`.
  - `tests/test_resource_manager.py`: `patch("app.resource_manager.requests.X", new=mock)`.
  - `app/routes/backups.py:restore_backup()`: `dirs_exist_ok=True`.
  - `tests/test_morph.py` **(НОВОЕ):** 16 тестов морфологического анализа pymorphy3.

## Настройка для контрибьюторов

При добавлении или изменении переменных окружения в `app/config.py` обновлять нужно и `.env`, и `.env.example`. В `.env` лежат реальные значения; в `.env.example` — плейсхолдеры и комментарии. Порядок секций должен совпадать.

При добавлении или изменении переменных окружения в `app/config.py` обновлять нужно и `.env`, и `.env.example`. В `.env` лежат реальные значения, в `.env.example` — плейсхолдеры и комментарии. Порядок секций должен совпадать, и секреты из `.env` никогда не должны попадать в `.env.example`. Полный справочник переменных — в [CONFIGURATION-ru.md](CONFIGURATION-ru.md).

## CLI-инструменты

```bash
docker exec flai-web flask admin-password <password>
docker exec flai-web flask cleanup-uploads
docker exec flai-web flask migrate-messages-format [--dry-run]
docker exec flai-web flask backfill-attachment-paths [--dry-run]
docker exec flai-web flask import-history-to-slm [--force] [user_id]
```

`backfill-attachment-paths` переносит на диск вложения старых сообщений чата, которые всё ещё лежат base64 внутри JSON-контента: каждая часть сохраняется через `save_uploaded_file` (первичная переиспользует `file_path` строки), полезная нагрузка заменяется сохранённым относительным путём, а колонка `file_data` очищается только там, где есть `file_path`. Коммит по строкам, идемпотентность (повторный запуск обновляет 0 строк), `--dry-run` только отчитывается без записи; сначала — резервная копия БД.

Обслуживание долговременной памяти обращается к контейнеру SLM напрямую:

```bash
# All users
docker exec flai-slm python3 -c "import urllib.request,json; urllib.request.urlopen(urllib.request.Request('http://localhost:8766/cleanup-memories',data=json.dumps({}).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=30).read().decode()"

# Single user
docker exec flai-slm python3 -c "import urllib.request,json; urllib.request.urlopen(urllib.request.Request('http://localhost:8766/cleanup-memories',data=json.dumps({'profile':'valery'}).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=30).read().decode()"
```

## Сервер разработки

```bash
python wsgi.py                  # 0.0.0.0:5000, debug=True
gunicorn -c gunicorn_config.py wsgi:app   # production: 1 worker, 900s timeout
```

## Документация

Документация уровня README ориентирована на пользователя и переводится. Документация уровня кода — только на английском. См. правило 6 в [../AGENTS.md](../AGENTS.md).
