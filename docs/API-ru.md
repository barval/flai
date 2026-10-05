# ПЛИИ OpenAI-совместимый API

ПЛИИ предоставляет небольшое, честное подмножество HTTP API OpenAI. Всё, что клиенту
нужно для интеграции с системой домашней автоматизации, ботом или скриптом,
работает через эти эндпоинты.

**Ключевое проектное решение:** ПЛИИ — это *роутер возможностей*, а не сервер моделей.
Запрос не отправляется весам, названным в `model`. Промпт уходит в роутер ПЛИИ ровно
так же, как из веб-чата, а роутер решает, нужен ли для ответа модель рассуждений,
поиск по документам, веб-поиск, модель зрения или генератор изображений. Именно
поэтому API умеет отдавать документы, изображения и результаты веб-поиска через
единственный эндпоинт в форме OpenAI и именно поэтому поле `model` принимается,
а затем игнорируется.

Всё выполняется в существующей очереди GPU, поэтому API подчиняется тем же правилам
сериализации и VRAM, что и веб-интерфейс: одна модель за раз, на одном GPU.

## Интерактивная документация

| Что | Где |
|---|---|
| Swagger UI (просмотр и проверка каждого эндпоинта) | `/v1/docs` |
| Документ OpenAPI 3.0.3 (машиночитаемый) | `/v1/openapi.json` |

Оба отдаются из `docs/openapi-v1.yaml` и работают **без ключа Bearer**, поскольку
спецификация — это не данные, — но каждая описанная ими операция требует ключа.
UI поставляется вместе с сервером, поэтому страница отрисовывается офлайн, без CDN.

> `docs/openapi.yaml` — это отдельный, более старый документ для внутреннего
> web/admin API. Он предшествует `/v1` и не отдаётся; для интеграций используйте
> `/v1/openapi.json`.

Этот файл — текстовый контракт; оба документа выше генерируются из того же списка
эндпоинтов и поддерживаются в актуальном состоянии тестом `tests/test_api_docs.py`.

---

## Статус

| Возможность | Эндпоинт | Состояние |
|---|---|---|
| API-ключи | Веб-интерфейс, `/api/api-keys/*` | Готово |
| Завершение чата (синхронно) | `POST /v1/chat/completions` | Готово |
| Завершение чата (поток SSE) | `POST /v1/chat/completions` с `stream: true` | Готово |
| Завершение чата (асинхронно) | `POST /v1/flai/chat/async` | Готово |
| Эмбеддинги | `POST /v1/embeddings` | Готово |
| Список моделей | `GET /v1/models` | Готово |
| Личность и возможности | `GET /v1/flai/me` | Готово |
| Синтез речи | `POST /v1/audio/speech` | Готово |
| Распознавание речи | `POST /v1/audio/transcriptions` | Готово |
| Список задач / статус / отмена | `/v1/flai/tasks*` | Готово |
| Скачивание медиа задачи | `GET /v1/flai/tasks/{task_id}/content` | Готово |
| Файлы и документы | `/v1/files*`, `/v1/flai/documents*` | Готово |
| Генерация и редактирование изображений | `POST /v1/images/generations`, `POST /v1/images/edits` | Готово |
| Генерация видео | `POST /v1/videos`, `GET /v1/videos/{task_id}` | Готово |
| Глубокий анализ (RLM) | `POST /v1/flai/rlm` | Готово |
| Сессии и история | `/v1/flai/sessions*` | Готово |
| Интерактивный справочник | `/v1/docs`, `/v1/openapi.json` | Готово |
| Документы, интерфейс глубокого анализа | Только веб-интерфейс | Веб-интерфейс |

Эндпоинтов, не перечисленных в этой таблице, пока не существует. Клиенту, которому
они понадобятся, стоит следить за `CHANGELOG.md`.

---

## Аутентификация

Аутентификация — это ключ API отдельного пользователя, передаваемый как Bearer-токен.
Ключи создаются в веб-интерфейсе (**API-ключи** в меню аккаунта): секрет показывается
ровно один раз, а хранится только его SHA-256 дайджест.

```bash
curl http://localhost:5000/v1/models \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx"
```

Правила, важные для интеграций:

- Ключ определяет **пользователя**. Каждый запрос выполняется с его моделями,
  документами, стилем ответов и языком.
- API **никогда** не устанавливает cookie и никогда не трогает веб-сессию Flask.
  Браузер с действительной веб-сессией остаётся авторизованным и отдельным; скрипт
  без сессии работает нормально.
- Отозванный ключ перестаёт работать со следующим запросом (`401 invalid_api_key`).
- `last_used_at` обновляется при каждом аутентифицированном запросе.

Весь blueprint `/v1` освобождён от CSRF-защиты, потому что Bearer-токен — это не
cookie. Все остальные маршруты сохраняют CSRF.

---

## Непрерывность сессии

Клиенты OpenAI не хранят состояние: они пересылают полный список сообщений при
каждом вызове. ПЛИИ умеет работать обоими способами, и выбор остаётся за клиентом.

**Без состояния (по умолчанию).** Отправьте всю историю в `messages`. ПЛИИ хранит
диалог в собственной сессии, и следующий ход продолжается с сохранённой истории,
поэтому клиент может вести себя как обычный клиент OpenAI.

**С состоянием.** Передайте устойчивое значение `user`:

```json
{"user": "home-assistant", "messages": [{"role": "user", "content": "What is in the kitchen?"}]}
```

ПЛИИ сопоставляет эту пару `(login, user)` с одной внутренней сессией чата и
использует её повторно. То же самое `user` при следующем вызове продолжает диалог;
другое значение начинает новый. Используйте одно значение `user` только в рамках
одной интеграции — два клиента с ним делят один диалог.

**Закрепление сессии.** Каждый ответ несёт `flai_session_id`. Отправьте его обратно
как `metadata.session_id`, чтобы продолжить именно эту сессию:

```json
{"metadata": {"session_id": "3f2b1c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"}, "messages": [{"role": "user", "content": "And the garden?"}]}
```

Идентификатор сессии, не принадлежащий владельцу ключа, отклоняется с
`404 session_not_found`. Именно это позволяет безопасно вести по одному диалогу
на комнату или на устройство.

Диалог также виден в веб-чате: ход из API сохраняется как обычная пара сообщений с
логином пользователя API, поэтому запрос, сделанный скриптом, появляется в его
истории и вызывает тот же индикатор непрочитанного, что и веб-сообщение.

---

## `POST /v1/chat/completions`

### Синхронный запрос

```bash
curl http://localhost:5000/v1/chat/completions \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
        "model": "flai-chat",
        "user": "home-assistant",
        "messages": [{"role": "user", "content": "Summarize the news about local elections"}]
      }'
```

```json
{
  "id": "chatcmpl-8f1c...",
  "object": "chat.completion",
  "created": 1759000000,
  "model": "flai-chat",
  "choices": [
    {"index": 0, "message": {"role": "assistant", "content": "..."}, "finish_reason": "stop"}
  ],
  "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
  "flai_session_id": "3f2b1c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
}
```

`usage` сообщает реальные количества токенов, записанные моделью, которая сгенерировала
ответ. Значение равно `0`, когда маршрут не использует токенизированную модель.

### Запрос с потоком

```bash
curl -N http://localhost:5000/v1/chat/completions \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
        "stream": true,
        "stream_options": {"include_usage": true},
        "messages": [{"role": "user", "content": "Write a haiku about VRAM"}]
      }'
```

Ответ приходит как `text/event-stream`. Чанки — это стандартные объекты
`chat.completion.chunk`; первый открывает роль ассистента, последний несёт
`finish_reason: "stop"`, а поток всегда завершается `data: [DONE]`.

```text
data: {"id":"chatcmpl-8f1c...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-8f1c...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"VRAM"},"finish_reason":null}]}

data: {"id":"chatcmpl-8f1c...","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"flai_session_id":"3f2b..."}

data: {"id":"chatcmpl-8f1c...","object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":31,"completion_tokens":24,"total_tokens":55}}

data: [DONE]
```

Подробности о стриминге, которые стоит знать:

- В поток идёт только ответ. События прогресса (`reasoning_thinking`, `routing`,
  `loading_model`) и токены, принадлежащие другой задаче, отбрасываются, поэтому
  клиент API никогда не увидит название стадии в тексте ассистента.
- Задача, которая перепоставляет себя в очередь медленного GPU-воркера,
  отслеживается молча. Клиент продолжает получать один непрерывный поток и
  получает единственный `finish_reason` — от задачи, которая дала финальный ответ.
- Комментарий keep-alive (`: ping`) отправляется каждые 15 секунд молчания, чтобы
  прокси не обрывали простаивающее соединение, пока грузится модель.
- Если клиент отключился, задача продолжает выполняться, и ответ всё равно попадёт
  в веб-чат. Прекращается только поток.
- `stream_options.include_usage` по умолчанию равен `false`. Установите его в `true`,
  чтобы получить финальный чанк с usage.

### Изображения

Изображение передаётся как часть контента `image_url` в формате OpenAI с инлайновым
URL вида `data:`:

```json
{
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "What is on this photo?"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQSk..."}}
      ]
    }
  ]
}
```

Ограничения: URL должен начинаться с `data:`, быть закодирован в base64 и иметь MIME-тип
вида `image/*`. Удалённые URL отклоняются с `400` — ПЛИИ работает офлайн и не будет
за вас скачивать изображение из интернета. Если частей-изображений несколько,
используется первая, а остальные отбрасываются.

### Принимаемые и игнорируемые параметры

Они принимаются, чтобы штатные клиенты OpenAI работали, и ничего не меняют:

| Параметр | Поведение |
|---|---|
| `model` | Игнорируется. Принимается любое значение, включая неизвестное. ПЛИИ маршрутизирует по возможностям. Ответ всегда сообщает `flai-chat`. |
| `stream_options` | `include_usage` учитывается; другие ключи игнорируются. |
| `tools`, `tool_choice`, `functions` | Игнорируются. ПЛИИ не вызывает внешних функций. |
| `response_format` | Игнорируется. Запрос JSON не заставляет отвечать JSON; вместо этого напишите в сообщении «ответь только в JSON». |
| `n`, `temperature`, `max_tokens`, `stop`, `presence_penalty` | Принимаются и игнорируются. Параметры генерации фиксированы для каждого маршрута. |
| `user` | Используется для непрерывности сессии (см. выше), а не в смысле OpenAI. |

---

## `POST /v1/flai/chat/async`

Чат по схеме «отправил и опрашивай»: запрос ставится в очередь точно так же, как
`/v1/chat/completions`, и немедленно получает ответ с идентификатором задачи, поэтому
клиент никогда не держит открытое HTTP-соединение, пока работает очередь GPU.

```bash
curl http://localhost:5000/v1/flai/chat/async \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"user": "client-42", "messages": [{"role": "user", "content": "Compare the contracts"}]}'
```

```json
HTTP/1.1 202 Accepted
{
  "id": "b6f1d0a2-4c77-4a1e-9c3f-2f1d8e5b7a10",
  "object": "flai.task",
  "status": "queued",
  "poll_url": "/v1/flai/tasks/b6f1d0a2-4c77-4a1e-9c3f-2f1d8e5b7a10"
}
```

Опрашивайте `poll_url` с тем же ключом Bearer:

```bash
curl http://localhost:5000/v1/flai/tasks/b6f1d0a2-4c77-4a1e-9c3f-2f1d8e5b7a10 \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx"
```

```json
{
  "id": "b6f1d0a2-4c77-4a1e-9c3f-2f1d8e5b7a10",
  "object": "flai.task",
  "status": "completed",
  "result": {
    "response": "Both contracts cover the same period…",
    "usage": {"prompt_tokens": 1120, "completion_tokens": 340}
  }
}
```

- Тело запроса такое же, как у `/v1/chat/completions`: `messages`, плюс `user` или
  `metadata.session_id` для непрерывности сессии. `model`, `stream`, `tools` и
  `response_format` принимаются и игнорируются (см. таблицу выше).
- Ход пользователя сохраняется и ставится в очередь до отправки ответа, поэтому
  ответ также появляется в веб-интерфейсе этой сессии.
- `status` проходит `queued` → `processing` → `completed` / `error` /
  `cancelled`; `result.response` содержит текст ответа.
- Отмена — это `POST /v1/flai/tasks/{task_id}/cancel`. У чат-задачи нет
  сгенерированного файла, поэтому `/content` к ней неприменим.
- Слот синхронного ожидания не занимается: запрос возвращается до запуска модели,
  поэтому этот эндпоинт не расходует `API_MAX_CONCURRENT_WAITS`.

---

## `POST /v1/embeddings`

```bash
curl http://localhost:5000/v1/embeddings \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"input": ["first document", "second document"], "encoding_format": "float"}'
```

```json
{
  "object": "list",
  "data": [
    {"object": "embedding", "index": 0, "embedding": [0.0123, -0.0456]},
    {"object": "embedding", "index": 1, "embedding": [0.0789, 0.0011]}
  ],
  "model": "flai-embeddings",
  "usage": {"prompt_tokens": 0, "total_tokens": 0}
}
```

- `input` принимает одну непустую строку или массив непустых строк. Массивы
  токенов не поддерживаются.
- `encoding_format` принимает `float` (по умолчанию) и `base64` (float32 с
  порядком байт little-endian, как на проводе у OpenAI).
- `model`, `dimensions` и `user` принимаются и игнорируются: у ПЛИИ одна модель
  эмбеддингов и одна размерность.
- Запрос не создаёт сессию чата и не сохраняет сообщение. Он всё равно проходит
  через очередь и занимает блокировку GPU, поэтому большая пачка встаёт в очередь
  за уже выполняющейся генерацией, а не конкурирует с ней.
- `usage` сообщается нулями: путь эмбеддингов не тарифицирует токены.

---

## `POST /v1/audio/speech`

Синтез речи настроенным TTS-бэкендом. Kokoro возвращает WAV, а Piper — MP3;
неподдерживаемые значения `response_format` отклоняются, а не молча помечаются
один формат как другой.

```bash
curl http://localhost:5000/v1/audio/speech \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"input":"Hello from FLAI"}' \
  --remote-name --remote-header-name
```

| Поле | Поведение |
|---|---|
| `input` | Обязательный непустой текст. |
| `voice` | Необязательное имя голоса, передаётся в Kokoro; Piper может его игнорировать. |
| `language`, `gender` | Необязательные расширения ПЛИИ; language принимает `ru` или `en`, gender принимает `male` или `female`; значения по умолчанию берутся из профиля владельца API-ключа. |
| `response_format` | По умолчанию — родной формат бэкенда (`wav` для Kokoro, `mp3` для Piper); остальные форматы дают `400 invalid_response_format`. |
| `model`, `speed` | Принимаются и игнорируются; ПЛИИ не выбирает модель TTS и не применяет изменение скорости. |

Ответ — это байты аудио с MIME-типом, возвращённым TTS-сервисом, и подходящим именем
вложения (`speech.wav` или `speech.mp3`). Синтез речи не хранит состояния и не
добавляет сообщение в чат пользователя. Недоступный модуль TTS возвращает `503`,
сбой синтеза — `500`.

---

## `POST /v1/audio/transcriptions`

Загрузите аудиофайл как `multipart/form-data`. Распознавание ставится в очередь через
существующую очередь и сервис Whisper, но, в отличие от голосового сообщения в вебе,
оно не создаёт сессию чата, не сохраняет сообщение с транскриптом и не публикует
результат в общем пользовательском SSE-канале.

```bash
curl http://localhost:5000/v1/audio/transcriptions \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -F "file=@meeting.wav" \
  -F "response_format=json"
```

| Поле | Поведение |
|---|---|
| `file` | Обязательная multipart-загрузка аудио; проверяется настроенным аудиомодулем. |
| `response_format` | `json` (по умолчанию, возвращает `{"text":"..."}`) или `text` (обычный текст UTF-8). `srt`, `vtt` и `verbose_json` не поддерживаются, потому что модуль Whisper не даёт временны́е метки. |
| `language` | Необязательное расширение ПЛИИ (`ru` или `en`); по умолчанию — язык владельца API-ключа. |
| `model` | Принимается и игнорируется; ПЛИИ использует настроенный сервис Whisper. |

Файлы больше `MAX_CONTENT_LENGTH` Flask получают `413 request_too_large` в конверте
ошибок OpenAI. Распознавание, превысившее `API_SYNC_MAX_WAIT`, возвращает
`408 task_timeout`; его задача в очереди продолжается, но этот эндпоинт без состояния
не сохраняет транскрипт в чат.

---

## Статус и отмена асинхронных задач

Долгие операции ПЛИИ можно опрашивать через реестр задач с проверкой владельца.
Идентификатор задачи — это не токен авторизации: каждое чтение и каждая отмена
проверяют, что ID зарегистрирован на владельца аутентифицированного API-ключа.

```bash
# List the most recent API tasks (limit is clamped to 1–100; default 20)
curl http://localhost:5000/v1/flai/tasks?limit=20 \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx"

# Inspect one task
curl http://localhost:5000/v1/flai/tasks/your-task-id \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx"

# Request cancellation of an active task
curl -X POST http://localhost:5000/v1/flai/tasks/your-task-id/cancel \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx"
```

- `GET /v1/flai/tasks` возвращает недавние задачи только этого владельца API-ключа.
- `GET /v1/flai/tasks/{task_id}` возвращает `queued`, `processing`, `completed`
  или `error`. Задача, перепоставленная в очередь под новым ID, отслеживается,
  а дочерний ID возвращается с `parent_task_id`; дочерняя задача с конфликтующими
  метаданными владения никогда не читается.
- Ответ по ожидающей задаче использует `position: null`, когда очередь не сообщает
  точную позицию этой задачи.
- Результаты — это метаданные из белого списка; пути файловой системы, байты
  загруженных файлов, токены и сырые payload'ы очереди никогда не возвращаются.
- Дочерняя задача, перепоставленная в очередь, отслеживается, только если её
  запись о владельце/сессии/эндпоинте совпадает с родительской. Конфликтующие
  или неполные метаданные дочерней задачи приводят к отказу (fail closed).
- Индекс списка задач ограничен 500 последними ID задач на каждого пользователя API;
  более старые записи задач всё равно истекают по `REDIS_RESULT_TTL`.
- `POST /v1/flai/tasks/{task_id}/cancel` возвращает `{"status":"cancelling"}`
  только для активной обрабатываемой задачи. Завершённые, упавшие и всё ещё
  стоящие в очереди задачи возвращают `409 task_not_cancellable`, потому что
  существующая очередь не может безопасно убрать элемент из очереди.
- Неизвестный ID или ID другого пользователя возвращает `404 task_not_found`,
  что не даёт использовать API как оракул владения.

Реестр задач хранится в Redis в течение `REDIS_RESULT_TTL` секунд — столько же,
сколько хранятся результаты очереди. Эндпоинты списка/статуса/отмены задач
расходуют бюджет `API_RATE_LIMIT` владельца API.

Пример ответа по задаче:

```json
{
  "id": "a1d5b76a-...",
  "object": "flai.task",
  "status": "completed",
  "endpoint": "/v1/videos",
  "created_at": 1780152000.5,
  "result": {"response": "⚠️ ...", "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
}
```

---

## Асинхронная генерация изображений и видео

Изображения и видео создаёт та же сериализованная очередь GPU, что и веб-чат:
эндпоинты ставят задачу в очередь и возвращают `202 Accepted` с URL для опроса.
Генерация модели никогда не выполняется внутри HTTP-запроса, а после постановки
в очередь асинхронная работа не расходует `API_MAX_CONCURRENT_WAITS`.

```bash
# Generate an image from a prompt
curl http://localhost:5000/v1/images/generations \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "a lighthouse at dusk", "model": "flai-image"}'

# Edit an uploaded image (multipart)
curl http://localhost:5000/v1/images/edits \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -F image=@photo.jpg \
  -F prompt="make it winter"

# Start a video generation
curl http://localhost:5000/v1/videos \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "waves on a beach", "width": 768, "height": 512, "num_frames": 96, "frame_rate": 12}'
```

- `response_format` должен быть `url` (по умолчанию). `b64_json` отклоняется с
  `400 invalid_request_error`: payload хранится на сервере, поэтому клиенты
  скачивают его по content-URL, а не получают мегабайты внутри ответа.
- Редактирование изображений требует multipart-загрузки `image` и непустого
  `prompt`; ссылок на файлы в виде пути никогда не принимается.
- Параметры видео проверяются по настроенному `VideoModule`, а не передаются как
  есть. Любой ключ, кроме `prompt`, `model`, `user` и `metadata`, должен быть
  **целым числом** в пределах своего диапазона; всё остальное отклоняется с
  `400 invalid_video_options`.

  | Параметр | Диапазон | По умолчанию |
  |---|---|---|
  | `width`, `height` | 256–1024, кратно 32 | 768, 512 |
  | `num_frames` | 9–240 | 240 |
  | `frame_rate` | 6, 12, 16 или 24 | 24 |
  | `seed` | -1 … 2³¹−1 | значение модели |

  Общее число пикселей ограничено `768x512x240`, а длительность — 10 с
  (`num_frames <= frame_rate * 10`); превышение любого из них — `400`.
- Когда модуль изображений или видео отсутствует или недоступен, эндпоинты
  возвращают `503 service_unavailable`.
- Каждый ответ несёт промпт в записи задачи, а ход диалога (промпт + созданное
  медиа) сохраняется в истории API-сессии.

Идентификатор задачи в теле `202` опрашивается через перечисленные выше эндпоинты
с проверкой владельца. Завершённая медиа-задача добавляет в ответ `content_url`,
а задачи видео дублируют его как `url` (форма видео-задачи OpenAI):

```json
{
  "id": "a1d5b76a-...",
  "object": "flai.task",
  "status": "completed",
  "endpoint": "/v1/images/generations",
  "content_url": "/v1/flai/tasks/a1d5b76a-.../content",
  "result": {"response": "Image generated", "usage": {"prompt_tokens": 12, "completion_tokens": 0}}
}
```

### `GET /v1/flai/tasks/{task_id}/content`

Скачивает созданное медиа для завершённой задачи. Проверка многослойна: задача
должна принадлежать аутентифицированному владельцу, а исходное сообщение запрашивается
с JOIN, доказывающим, что сессия принадлежит тому же владельцу. Файл определяется
через `realpath` внутри `UPLOAD_FOLDER`; переданные вызывающим пути никогда не
принимаются, поэтому обход каталогов или чужая задача дают `404 task_not_found`
без оракула по файловой системе. Ответ — это байты `send_file` с сохранённым
MIME-типом и безопасным именем файла.

---

## Файлы и документы

Документы пользователя доступны и через OpenAI-совместимые маршруты `/v1/files`,
и через псевдонимы ПЛИИ под `/v1/flai/documents`. Оба семейства вызывают одни и те
же обработчики, поэтому поведение владения, квот и валидации одинаково.

```bash
# List your documents
curl http://localhost:5000/v1/files -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx"

# Upload a document (multipart). It is validated, stored and queued for indexing.
curl http://localhost:5000/v1/files \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -F file=@report.pdf

# Fetch metadata / download / delete
curl http://localhost:5000/v1/files/{file_id} -H "Authorization: Bearer flai_..."
curl http://localhost:5000/v1/files/{file_id}/content -H "Authorization: Bearer flai_..." -o report.pdf
curl -X DELETE http://localhost:5000/v1/files/{file_id} -H "Authorization: Bearer flai_..."
```

- Записи списка и метаданных содержат только `id`, `object: "file"`, `filename`,
  `bytes`, `created_at`, `purpose: "assistants"` и расширение ПЛИИ
  `index_status` (`pending`, `processing`, `indexed`, `error`).
- Загрузки переиспользуют веб-цепочку валидации: разрешённое расширение + магические
  байты, `MAX_DOCUMENT_SIZE_MB` (`413`), квота документов пользователя (`413`).
  Изображения автоматически уменьшаются и конвертируются точно так же, как при
  загрузке из чата.
- Имена файлов в хранилище — UUID внутри `DOCUMENTS_FOLDER/<login>/`; ответы никогда
  не содержат путей файловой системы.
- Эндпоинт скачивания определяет сохранённый относительный путь с проверкой
  вхождения через `realpath`; чужие и неизвестные ID дают один и тот же
  `404 file_not_found`.
- Удаление убирает запись индекса RAG (если модуль RAG доступен), хранимый файл и
  запись в базе — и только у владельца.

---

## Глубокий анализ (RLM)

`POST /v1/flai/rlm` запускает явную задачу глубокого анализа по принадлежащим
вызывающему документам (и, по желанию, по одному загруженному изображению) через ту
же сериализованную по GPU очередь, что и веб-чат. Требуется `RLM_ENABLED`; иначе
эндпоинт возвращает `403 rlm_disabled`, а `GET /v1/flai/me` сообщает `rlm: false`.

```bash
curl http://localhost:5000/v1/flai/rlm \
  -H "Authorization: Bearer flai_xxxxxxxxxxxxxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
        "session_id": "3f2b1c4d-...",
        "doc_ids": ["a1b2c3d4-..."],
        "question": "Compare the risk sections of these contracts"
      }'
```

- `session_id` должен быть собственной сессией (иначе `404 session_not_found`),
  и каждая запись в `doc_ids` должна принадлежать пользователю
  (`404 document_not_found`); требуется хотя бы один документ или загруженное
  изображение (`400`).
- Многочастный вариант принимает поля формы `files` (до `MAX_CHAT_IMAGES`
  изображений; одиночное поле `file` по-прежнему работает), `session_id`,
  `doc_ids` (строка с JSON-массивом) и `question`; каждое изображение проходит те же
  квоты и ограничение по уменьшению, что и загрузки из чата (при нескольких
  картинках действует лимит `MAX_IMAGE_SIZE_MULTI`), и сохраняется вместе с вопросом.
- Ход пользователя сохраняется в истории API-сессии, а ответ — `202 Accepted`
  с `task_id`, `position` и `user_message_id`.
- Опрашивайте возвращённый `task_id` через `/v1/flai/tasks/{task_id}` — задача
  автоматически регистрируется в индексе владельца.

---

## Сессии и история

Диалоги через API — это обычные сессии чата ПЛИИ; эти эндпоинты позволяют клиенту
перечислять, создавать и читать их, не трогая указатель на последнюю сессию веб-интерфейса.

```bash
# List sessions owned by the key owner
curl http://localhost:5000/v1/flai/sessions -H "Authorization: Bearer flai_..."

# Create a session (optional title; defaults to a localized "New session")
curl -X POST http://localhost:5000/v1/flai/sessions \
  -H "Authorization: Bearer flai_..." \
  -H "Content-Type: application/json" \
  -d '{"title": "Support bot"}'

# Read history (limit clamped to 1–200, offset >= 0)
curl "http://localhost:5000/v1/flai/sessions/{session_id}/messages?limit=50&offset=0" \
  -H "Authorization: Bearer flai_..."
```

- Ответы списка имеют вид `{"object": "list", "data": [{id, title, created_at}, ...]}`.
- Ответы с сообщениями повторяют форму веб-API:
  `{"messages": [...], "limit", "offset", "has_more"}`; payload'ы файлов, уже
  сохранённые на диске, вычищаются из `file_data` общим помощником БД.
- Чужие или неизвестные идентификаторы сессий возвращают `404 session_not_found`.
- Ответы `/v1` никогда не устанавливают cookie: личность API локальна для запроса.

---

## `GET /v1/models`

Возвращает идентификаторы возможностей ПЛИИ в виде объектов моделей OpenAI. Этот
список — каталог возможностей, а не набор весов: обработку любого из этих
идентификаторов может взять на себя роутер, а передача одного из них как `model`
ничего не меняет.

```json
{"object": "list", "data": [{"id": "flai-chat", "object": "model", "owned_by": "flai", "description": "Router-selected chat"}, {"id": "flai-tts", "object": "model", "owned_by": "flai", "description": "Speech synthesis"}, {"id": "flai-stt", "object": "model", "owned_by": "flai", "description": "Speech transcription"}]}
```

## `GET /v1/flai/me`

Личность и флаги возможностей владельца ключа — полезно экрану настройки, который
хочет знать, что поддерживает сервер, до того как строить запрос.

```json
{
  "login": "valery",
  "service_class": 2,
  "is_admin": true,
  "language": "en",
  "response_style": "neutral",
  "capabilities": {
    "chat_completions": true,
    "streaming": true,
    "embeddings": true,
    "audio_speech": true,
    "audio_transcriptions": true,
    "images": true,
    "videos": true,
    "documents": true,
    "rlm": true,
    "tools": false,
    "response_format_json_schema": false
  }
}
```

Два аудиофлага равны true только тогда, когда настроенные модули TTS/аудио доступны;
`flai-tts` и `flai-stt` — это идентификаторы возможностей в списке моделей, а не
выбираемые веса. `images` и `videos` равны true только тогда, когда доступны и
модуль изображений/видео, **и** мультимодальная модель (используется для обогащения
промпта).

---

## Ошибки

Каждая ошибка использует конверт OpenAI, и каждое сообщение начинается с `⚠️ `:

```json
{
  "error": {
    "message": "⚠️ Chat session not found",
    "type": "invalid_request_error",
    "param": null,
    "code": "session_not_found"
  }
}
```

Сообщения локализованы на язык пользователя API, поэтому англоязычный пользователь
получает английские ошибки, а русскоязычный — русские. Машиночитаемая часть — это
всегда `code`.

| Статус | `type` | `code` | Причина |
|---|---|---|---|
| 400 | `invalid_request_error` | `invalid_body` | Тело не является JSON-объектом |
| 400 | `invalid_request_error` | `invalid_request` | `messages` или часть контента некорректны |
| 400 | `invalid_request_error` | `invalid_user`, `invalid_session_id`, `invalid_stream` | У поля неверный тип |
| 400 | `invalid_request_error` | `invalid_input`, `invalid_encoding_format` | Поле эмбеддингов непригодно для использования |
| 400 | `invalid_request_error` | `invalid_limit` | Лимит списка задач не является целым числом |
| 401 | `invalid_request_error` | `invalid_api_key` | Ключ отсутствует, некорректен, неизвестен или отозван |
| 404 | `invalid_request_error` | `session_not_found` | `metadata.session_id` принадлежит другому пользователю |
| 404 | `invalid_request_error` | `task_not_found` | ID задачи неизвестен или принадлежит другому пользователю API |
| 405 | `invalid_request_error` | `method_not_allowed` | Неверный HTTP-метод |
| 408 | `server_error` | `task_timeout` | Задача всё ещё работает после `API_SYNC_MAX_WAIT`; она **не** отменена |
| 409 | `invalid_request_error` | `task_not_cancellable` | Задача в конечном состоянии или всё ещё в очереди |
| 413 | `invalid_request_error` | `request_too_large` | Загрузка превышает `MAX_CONTENT_LENGTH` Flask |
| 429 | `rate_limit_error` | `rate_limit_exceeded` | Бюджет запросов владельца ключа исчерпан |
| 429 | `rate_limit_error` | `too_many_requests` | Уже ожидает `API_MAX_CONCURRENT_WAITS` запросов (`Retry-After: 1`) |
| 500 | `server_error` | `task_failed` | Чат-задача завершилась ошибкой |
| 502 | `server_error` | `task_failed` | Задача эмбеддингов завершилась ошибкой или не вернула векторы |
| 503 | `service_unavailable` | `api_disabled` | На сервере `API_ENABLED=false` |

В потоке ошибка задачи приходит как SSE-объект `error`, за которым следует
`data: [DONE]`, а не как HTTP-статус — строка статуса к этому моменту уже отправлена.

---

## Таймауты, очередь и лимиты

ПЛИИ работает на одном потребительском GPU. Запросы сериализованы, и запрос,
которому нужна нерезидентная модель, сначала платит за её загрузку. Учитывайте
это при выборе клиентских таймаутов:

- Короткий ответ в чате обычно занимает несколько секунд; ответ с рассуждениями,
  веб-поиск, изображение или видео занимают больше.
- `API_SYNC_MAX_WAIT` (по умолчанию `600` секунд) ограничивает синхронное ожидание.
  По истечении API возвращает `408` с ID задачи в сообщении. Задача продолжает
  выполняться и не отменяется; ответы чата всё равно сохраняются в диалоге, а
  эндпоинты без состояния не создают сообщений чата.
- Держите клиентский таймаут выше `API_SYNC_MAX_WAIT` или задайте `API_SYNC_MAX_WAIT`
  ниже таймаута gunicorn (900 с), чтобы сервер всегда отвечал первым. Именно такая
  расстановка используется по умолчанию.
- Параллельные клиенты не выполняются на GPU одновременно; они стоят в очереди.
  Не повторяйте агрессивно при `408` — медленный ответ обычно уже выполняется.
- `API_MAX_CONCURRENT_WAITS` (по умолчанию `64`) ограничивает, сколько запросов
  `/v1` могут одновременно ждать задачу в очереди. Ожидающий запрос держит поток
  запроса до `API_SYNC_MAX_WAIT`, поэтому всплеск клиентов иначе навалился бы на
  сервер с одним воркером. При достижении предела API отвечает `429` с
  `Retry-After: 1` и `code: too_many_requests` **до** постановки в очередь, поэтому
  ничего не остаётся работать в фоне. Отклонённый, истёкший или завершённый запрос
  сразу возвращает свой слот. Асинхронные работы (изображение, видео) не держат
  слот после постановки в очередь.
- `API_RATE_LIMIT` (по умолчанию `60 per minute;1000 per hour`) ограничивает
  `POST /v1/chat/completions`, `POST /v1/embeddings`, `/v1/audio/*`,
  `/v1/images/*`, `/v1/videos*`, `/v1/flai/tasks*`, `/v1/flai/chat/async`,
  `/v1/files*`, `/v1/flai/documents*`, `POST /v1/flai/rlm` и
  `/v1/flai/sessions*`.
  Бюджет считается
  на каждого **владельца** API-ключа, а не на ключ и не на IP: несколько ключей
  одного пользователя делят его, и один шумный клиент не потратит чужую квоту.
  Исчерпанный бюджет возвращает `429 rate_limit_exceeded` до постановки любой
  задачи в очередь, поэтому ничего не остаётся работать в фоне. `GET /v1/models`
  и `GET /v1/flai/me` не ограничены.

### Браузерные клиенты (CORS)

Страница в браузере может прочитать ответ с другого источника, только если сервер
отправляет `Access-Control-Allow-Origin`, поэтому `/v1` отвечает на preflight-запросы
для разрешённых вами источников:

```
API_CORS_ORIGINS=https://home.example, https://tools.example
```

- По умолчанию список пуст, что держит API закрытым для браузеров.
- Источники сопоставляются **точно**, а не по префиксу, поэтому
  `https://home.example` не допускает `https://home.example.evil.example`.
- Заголовки добавляются только к `/v1/*`. Веб-интерфейс никогда не получает
  разрешающий заголовок, поэтому список нельзя использовать для чтения чата с другого
  источника.
- Разрешённые методы — `GET, POST, OPTIONS`, разрешённые заголовки запроса —
  `Authorization` и `Content-Type`; preflight кэшируется на 600 с.

---

## Конфигурация

| Переменная | По умолчанию | Значение |
|---|---|---|
| `API_ENABLED` | `true` | Главный переключатель. При `false` любой запрос `/v1` возвращает `503 api_disabled`. |
| `API_SYNC_MAX_WAIT` | `600` | Сколько секунд синхронный или стриминговый запрос ждёт свою задачу. |
| `API_RATE_LIMIT` | `60 per minute;1000 per hour` | Бюджет запросов на владельца ключа для чата, эмбеддингов, аудио, генерации медиа, файлов/документов, глубокого анализа, сессий и эндпоинтов управления задачами. Сообщается в `GET /v1/flai/me`. |
| `API_MAX_CONCURRENT_WAITS` | `64` | Сколько запросов могут одновременно ждать задачу в очереди. Сообщается в `GET /v1/flai/me`. |
| `API_CORS_ORIGINS` | _(пусто)_ | Точные источники через запятую, которым разрешено вызывать `/v1` из браузера. Пусто означает отсутствие доступа из браузеров. |

Все пять задокументированы в `.env.example`. Их изменение требует перезапуска контейнера.

---

## Замечания по безопасности

- Ключи хранятся как дайджесты SHA-256; дамп базы данных не раскрывает пригодный
  для использования ключ.
- Каждый запрос заново проверяет владельца сессии перед обращением к сессии, поэтому
  угаданный `session_id` не даст попасть в чужой диалог.
- API работает в том же процессе, что и веб-приложение, и освобождён от CSRF (он
  не может нести cookie сессии), но для всего требуется Bearer-токен. Его собственный
  бюджет считается на владельца ключа и отделён от веб-лимитера на вход.
- Поставьте API за обратным прокси с TLS, прежде чем открывать его за пределами
  вашей локальной сети: ключи передаются в заголовке `Authorization` открытым текстом
  по HTTP.
