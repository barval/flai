# Долговременная память (SLM)

ПЛИИ помнит факты между сессиями. У каждого пользователя есть постоянный профиль памяти; факты, извлечённые из разговора, возвращаются в контекст рассуждений последующих запросов.

Движок — **SuperLocalMemory (SLM)**, опциональный sidecar-контейнер. Логика извлечения и слияния, которая решает, *что* вообще стоит запоминать, живёт в самом ПЛИИ (`app/slm_rules.py`) и работает на CPU, без вызовов LLM.

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

1. **Проверка кандидата (CPU, без модели).** После ответа обмен ставится в очередь, только
   если сообщение похоже на личное (маркер первого лица или явное «запомни»). Это фильтр
   стоимости, а не роутер: пропущенный факт дешевле, чем вызов модели на каждом сообщении
   про погоду.
2. **Извлечение (фоновая очередь, резидентная мультимодальная модель).** Строгая JSON-схема
   просит только устойчивые сведения о самом пользователе — профиль, предпочтения, проекты,
   постоянные инструкции. Вопросы, текст задач, ответ ассистента, разовые числа и факты о
   третьих лицах отбрасываются и промптом, и парсером (`app/memory_facts.py`), а записи с
   низкой уверенностью вообще не доходят до хранилища. Экстрактор на правилах, который это
   заменило, срабатывал на 0 из 30 реальных сообщений.
3. **Дедупликация и слияние.** Новый факт сравнивается с хранилищем (сходство `0.85`) до
   записи; почти-дубликаты свёртываются по расстоянию редактирования, а факты-фрагменты
   вливаются в более сильное утверждение.
4. **Вспоминание.** Блок профиля (самые новые факты `kind=profile`) подставляется в каждый
   запрос независимо от формулировки; остальные факты — только выше `SLM_MIN_RECALL_SCORE`
   (0.5), поэтому нерелевантная фраза не стоит ни токенов, ни внимания модели. Всего до
   `SLM_RECALL_LIMIT` фактов.

Неверный факт хуже отсутствующего: до изменения в хранилище лежали промпты генерации
изображений и арифметические задачи, и recall возвращал их с 0.63 на вопрос «я люблю яблоки?».

## Настройка

```bash
# Recall
SLM_URL=http://flai-slm:8766
SLM_RECALL_LIMIT=7
SLM_MIN_RECALL_SCORE=0.5
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

Каждой учётной записи ПЛИИ принадлежит один профиль SLM, ключом служит логин пользователя. Удаление пользователя стирает соответствующий профиль SLM в той же операции (`userdb.delete_user()` вызывает маршрут `/delete-profile` обёртки и предупреждает, если это не удалось), а также каталог профиля на диске `data/slm/<login>/`. Это часть процедуры стирания по GDPR — см. [ADMINISTRATION-ru.md](ADMINISTRATION-ru.md).

## Обслуживание

Посмотреть, что лежит в памяти и что реально попадёт в промпт:

```bash
docker exec flai-web flask memory-report --user <login> --query "чего я не ем?"
```

Удалить факты одного источника (импорт истории сохранял каждое сообщение дословно):

```bash
docker exec flai-web flask memory-purge --user <login> --source auto_import        # пробный прогон
docker exec flai-web flask memory-purge --user <login> --source auto_import --yes  # удалить
```

Импорт прошлых диалогов в память:

```bash
docker exec flai-web flask import-history-to-slm [--force] [user_id]
```

Очистка и слияние воспоминаний — либо для всех пользователей, либо для одного профиля:

```bash
# All users
docker exec flai-slm python3 -c "import urllib.request,json; urllib.request.urlopen(urllib.request.Request('http://localhost:8766/cleanup-memories',data=json.dumps({}).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=30).read().decode()"

# Single user
docker exec flai-slm python3 -c "import urllib.request,json; urllib.request.urlopen(urllib.request.Request('http://localhost:8766/cleanup-memories',data=json.dumps({'profile':'<login>'}).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=30).read().decode()"
```

В контейнере SLM работают два процесса: демон SuperLocalMemory на **8765** и обёртка профилей, с которой общается FLAI, — на **8766**. Healthcheck опрашивает обёртку, и это единственный порт, который использует веб-контейнер. Проверяйте его, прежде чем считать память сломанной:

```bash
docker inspect --format '{{.State.Health.Status}}' flai-slm
docker logs flai-slm --tail 50
```

## Смотрите также

- [DOCUMENTS-ru.md](DOCUMENTS-ru.md) — RAG по загруженным документам (другой механизм: векторный поиск по файлам, а не выученные факты)
- [ADMINISTRATION-ru.md](ADMINISTRATION-ru.md) — удаление пользователей и процедура стирания по GDPR
- [../CHANGELOG.md](../CHANGELOG.md) — когда появились извлечение и слияние на основе правил
