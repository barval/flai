# Мониторинг и состояние системы

## Эндпоинт проверки состояния (Health Check)

```bash
curl http://localhost:5000/health
```

**Ответ:**
```json
{
  "status": "ok",
  "timestamp": "2026-04-08T23:00:00.000000+00:00",
  "services": {
    "web": "ok",
    "database": "ok",
    "redis": "ok",
    "llamacpp": "ok",
    "qdrant": "ok",
    "sd_wrapper": "ok",
    "whisper": "ok",
    "ltx_video": "ok"
  }
}
```

## Метрики Prometheus

```bash
curl http://localhost:5000/metrics
```
