# Room Snapshot API — Руководство по развёртыванию

## Обзор

Сервис **Room Snapshot API** предоставляет HTTP-доступ к снимкам IP-камер для приложения FLAI.
Код сервиса находится в поддиректории `room-snapshot-api/` (клонируется из отдельного репозитория).

**Два варианта развёртывания:**

1. **Локальное** — на том же сервере что и FLAI
2. **Удалённое** — на отдельном сервере

## Структура файлов

```
services/room-snapshot-api/
├── README.md                           ← Этот файл
├── deploy.sh                           ← Скрипт развёртывания
├── docker-compose-local.yml            ← Локальный docker-compose
├── docker-compose-remote.yml           ← Удалённый docker-compose
└── room-snapshot-api/                  ← Код сервиса (клон репозитория)
    ├── Dockerfile
    ├── app/
    ├── config/cameras.conf             ← Конфигурация камер
    ├── requirements.txt
    └── .env
```

## Быстрый старт

### 1. Клонирование кода сервиса (если ещё не клонирован)

```bash
cd /home/GIT/GITEA/BARVAL-MY/flai/services/room-snapshot-api
git clone https://github.com/barval/room-snapshot-api.git room-snapshot-api
```

### 2. Настройка камер

Отредактируйте `room-snapshot-api/config/cameras.conf`:

```conf
# Формат: код=ip:порт:название
spa=192.168.131.101:554/stream1:Спальня
gos=192.168.131.102:554/stream1:Гостиная
kab=192.168.131.103:554/stream1:Кабинет
```

В `room-snapshot-api/.env` укажите `RTSP_AUTH` — логин:пароль от камер:

```bash
cp room-snapshot-api/.env.example room-snapshot-api/.env
nano room-snapshot-api/.env
```

```env
RTSP_AUTH="admin:password"
FLASK_DEBUG=false
```

### 3. Развёртывание

Скрипт `deploy.sh` автоматизирует весь процесс:

```bash
# Локальное развёртывание (на том же сервере что и FLAI)
./deploy.sh local

# Удалённое развёртывание (отдельный сервер)
./deploy.sh remote
```

Полезные команды:

```bash
./deploy.sh status     # Показать статус сервиса
./deploy.sh logs       # Просмотр логов в реальном времени
./deploy.sh restart    # Перезапуск
./deploy.sh stop       # Остановка
```

Скрипт автоматически:
- Создаёт `.env` с безопасным `SECRET_KEY` (если отсутствует или дефолтный)
- Проверяет/создаёт Docker-сеть `flai_flai_network` (для локального режима)
- Собирает и запускает контейнер
- Ждёт готовности сервиса (до 15 попыток, проверка `/health`)
- Выводит статус, JSON health-ответа и дальнейшие инструкции

### 4. Подключение к FLAI

В `.env` основного приложения FLAI укажите:

```bash
# Локальное развёртывание (Docker-сеть, внутренний порт 5000)
CAMERA_API_URL=http://flai-room-snapshot-api:5000
CAMERA_ENABLED=true
CAMERA_API_TIMEOUT=15
CAMERA_CHECK_INTERVAL=30
```

Перезапустите FLAI:

```bash
docker compose -f docker-compose.gpu.yml restart web
```

## Порты

| Порт | Назначение |
|------|------------|
| `5000` | Внутренний порт контейнера (для Docker-сети) |
| `5005` | Внешний порт (маппинг на хост) |

> **Важно**: При подключении из контейнера FLAI через Docker-сеть используйте порт **5000**.
> При подключении с хоста (curl, браузер) — порт **5005**.

## Эндпоинты API

| Эндпоинт | Метод | Описание |
|----------|-------|----------|
| `/health` | GET | Проверка работоспособности |
| `/rooms` | GET | Список доступных камер |
| `/rooms/<код>` | GET | Информация о конкретной камере |
| `/snapshot/<код>` | GET | Снимок с камеры (JPEG) |
| `/info` | GET | Информация об API |

### Примеры

```bash
# Проверка здоровья
curl http://localhost:5005/health

# Список камер
curl http://localhost:5005/rooms

# Сохранить снимок
curl http://localhost:5005/snapshot/gos -o gos.jpg

# Информация о камере
curl http://localhost:5005/rooms/gos
```

## Мониторинг

```bash
# Логи
./deploy.sh logs

# Статус контейнера
docker ps --filter name=room-snapshot-api

# Потребление ресурсов
docker stats flai-room-snapshot-api
```

## Устранение неполадок

### Контейнер не запускается

```bash
# Логи
docker compose -f docker-compose-local.yml logs --tail=50

# Проверить наличие конфига камер
ls -la room-snapshot-api/config/cameras.conf

# Проверить .env
cat room-snapshot-api/.env
```

### Не удаётся получить снимок

1. Проверьте доступность камеры: `ping IP_КАМЕРЫ`
2. Проверьте логи: `./deploy.sh logs`
3. Убедитесь в правильности `RTSP_AUTH` в `.env`
4. Проверьте формат `cameras.conf` (код=ip:порт:название)

### FLAI не видит камеры

1. Убедитесь что `CAMERA_API_URL` в `.env` FLAI указывает на порт **5000** (не 5005)
2. Проверьте что контейнеры в одной Docker-сети:
   ```bash
   docker network inspect flai_flai_network
   ```
3. Проверьте из контейнера FLAI:
   ```bash
   docker exec flai-web python3 -c "import requests; r=requests.get('http://flai-room-snapshot-api:5000/health'); print(r.json())"
   ```

### Сервис не отвечает на порту 5005

```bash
# Проверить контейнер
docker ps --filter name=room-snapshot-api

# Проверить маппинг портов
docker port flai-room-snapshot-api

# Проверить health
curl http://localhost:5005/health
```

## Безопасность

### Чек-лист продакшена

- [ ] Надёжный `SECRET_KEY` (генерируется автоматически скриптом deploy.sh)
- [ ] `FLASK_DEBUG=false`
- [ ] Фаервол: доступ к порту 5005 только от FLAI-сервера
- [ ] HTTPS через reverse proxy (nginx)
- [ ] Актуальные прошивки камер
- [ ] Регулярные обновления безопасности

### Фаервол (удалённое развёртывание)

```bash
# Разрешить доступ только с сервера FLAI
sudo ufw allow from <flai-server-ip> to any port 5005

# Запретить остальным
sudo ufw deny 5005/tcp
```

## Бэкап конфигурации

```bash
# Бэкап конфига камер
tar -czf camera-config-backup.tar.gz room-snapshot-api/config/

# Бэкап .env
cp room-snapshot-api/.env room-snapshot-api/.env.backup
```
