# Долговременная память (SLM)

FLAI помнит факты между сессиями. У каждого пользователя есть постоянный профиль памяти; факты, извлечённые из разговора, возвращаются в контекст рассуждений последующих запросов.

Движок — **SuperLocalMemory (SLM)**, опциональный sidecar-контейнер. Логика извлечения и слияния, которая решает, *что* вообще стоит запоминать, живёт в самом FLAI (`app/slm_rules.py`) и работает на CPU, без вызовов LLM.

## Включение

Память опциональна и по умолчанию выключена. Запустите профиль:

```bash
docker compose -f docker-compose.gpu.yml --profile with-slm up -d
```

Затем задайте переменные в `.env`:

```bash
SLM_URL=http://flai-slm:8766
SLM_RECALL_LIMIT=7
```

Перезапустите веб-контейнер, чтобы он подхватил `SLM_URL`:

```bash
docker compose -f docker-compose.gpu.yml restart web
```

Без `SLM_URL` инстанс полностью работоспособен — факты просто никогда не извлекаются, и в промпты не подставляются факты из памяти.

## Как запоминается факт

1. **Извлечение (фон, только CPU).** После того как на сообщение пользователя дан ответ, фоновый поток прогоняет по тексту сообщения экстрактор на основе правил. Он разбивает текст на предложения, оценивает их по паттернам категорий (предпочтения, факты, инструкции, особенности личности) и оставляет то, что набрало достаточно высокий балл. LLM не участвует — именно поэтому извлечение не занимает слот GPU и не учитывается в квоте пользователя.
2. **Дедупликация и слияние (фоновая очередь).** Новые факты сравниваются с уже имеющимися. Семантическое сходство выше `SLM_SIMILARITY_THRESHOLD` (0.85) означает «один и тот же факт», и такая пара сливается вместо двойного сохранения. Почти-дубликаты дополнительно свёртываются по расстоянию редактирования, а факты-фрагменты вливаются в более сильное существующее утверждение.
3. **Затухание во времени.** Факты старше `SLM_TEMPORAL_DECAY_DAYS` (90) с уверенностью ниже `SLM_MIN_CONFIDENCE_FOR_DECAY` (0.5) архивируются автоматически.
4. **Вспоминание.** Когда модель рассуждений строит контекст, первыми забираются до `SLM_RECALL_LIMIT` фактов, и их реальная токеновая стоимость измеряется до добавления всего остального. Дополнительно роутер видит до `ROUTER_SLM_FACTS` (2) фактов, решая, о чём сообщение.

Явный запрос запомнить что-то — «запомни, что я не ем грибы» — разбирается отдельным путём явного запоминания в экстракторе, а не остаётся на откуп эвристикам оценки.

## Настройка

```bash
# Recall
SLM_URL=http://flai-slm:8766
SLM_RECALL_LIMIT=7
ROUTER_SLM_FACTS=2

# Rule-based extraction and merge
SLM_SIMILARITY_THRESHOLD=0.85
SLM_TEMPORAL_DECAY_DAYS=90
SLM_MIN_CONFIDENCE_FOR_DECAY=0.5

# Background merge job
MERGE_MAX_FACTS=100
MERGE_CONTEXT_SIZE=4096
MERGE_FACT_MAX_CHARS=120
MERGE_MAX_FIT_FACTS=62
```

`MERGE_MAX_FIT_FACTS` рассчитывается автоматически из `MERGE_CONTEXT_SIZE` — задавайте его, только если нужно переопределить значение.

## У каждого пользователя своя память

Каждой учётной записи FLAI принадлежит один профиль SLM, ключом служит логин пользователя. Удаление пользователя стирает соответствующий профиль SLM в той же операции (`userdb.delete_user()` вызывает маршрут `/delete-profile` обёртки и предупреждает, если это не удалось), а также каталог профиля на диске `data/slm/<login>/`. Это часть процедуры стирания по GDPR — см. [ADMINISTRATION-ru.md](ADMINISTRATION-ru.md).

## Обслуживание

Импорт прошлых диалогов в память:

```bash
docker exec flai-web flask import-history-to-slm [--force] [user_id]
```

Очистка и слияние воспоминаний — либо для всех пользователей, либо для одного профиля:

```bash
# All users
docker exec flai-slm python3 -c "import urllib.request,json; urllib.request.urlopen(urllib.request.Request('http://localhost:8766/cleanup-memories',data=json.dumps({}).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=30).read().decode()"

# Single user
docker exec flai-slm python3 -c "import urllib.request,json; urllib.request.urlopen(urllib.request.Request('http://localhost:8766/cleanup-memories',data=json.dumps({'profile':'valery'}).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=30).read().decode()"
```

Контейнер SLM здоров, если отвечает `GET http://localhost:8766/health`. В `docker-compose.gpu.yml` его healthcheck опрашивает порт **8766**, тогда как контейнер настроен с `SLM_PORT=8765`, — не считайте память сломанной, пока не посмотрите вывод healthcheck:

```bash
docker inspect --format '{{.State.Health.Status}}' flai-slm
docker logs flai-slm --tail 50
```

## Смотрите также

- [DOCUMENTS-ru.md](DOCUMENTS-ru.md) — RAG по загруженным документам (другой механизм: векторный поиск по файлам, а не выученные факты)
- [ADMINISTRATION-ru.md](ADMINISTRATION-ru.md) — удаление пользователей и процедура стирания по GDPR
- [../CHANGELOG.md](../CHANGELOG.md) — когда появились извлечение и слияние на основе правил
