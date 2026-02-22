
#!/bin/bash
# init.sh — Скрипт первичной настройки ИИ Локальный

set -e

echo "🤖 ИИ Локальный — Первичная настройка"
echo "======================================"

# Проверка Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker не установлен. Установите Docker и Docker Compose."
    exit 1
fi

# Проверка docker-compose
if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo "❌ Docker Compose не установлен."
    exit 1
fi

# Создание .env если не существует
if [ ! -f ".env" ]; then
    echo "📝 Создаю .env из шаблона..."
    cp .env.example .env
    echo "✅ .env создан. Отредактируйте файл под вашу систему."
else
    echo "✅ .env уже существует."
fi

# Проверка Ollama
echo ""
echo "🔍 Проверка Ollama..."
if curl -s http://localhost:11434/api/tags &> /dev/null; then
    echo "✅ Ollama доступен на http://localhost:11434"
    
    # Проверка моделей
    echo "📋 Доступные модели Ollama:"
    curl -s http://localhost:11434/api/tags | python3 -c "import sys,json; [print(f'  - {m[\"name\"]}') for m in json.load(sys.stdin).get('models', [])]" 2>/dev/null || echo "  (не удалось распарсить)"
else
    echo "⚠️ Ollama не доступен на http://localhost:11434"
    echo "   Убедитесь, что Ollama запущен и настроен для доступа из Docker."
fi

# Проверка Automatic1111
echo ""
echo "🔍 Проверка Automatic1111..."
if curl -s http://localhost:7860/sdapi/v1/options &> /dev/null; then
    echo "✅ Automatic1111 доступен на http://localhost:7860"
else
    echo "ℹ️ Automatic1111 не обнаружен (опционально)"
fi

# Создание директорий
echo ""
echo "📁 Создание директорий..."
mkdir -p backend/storage/{uploads,sessions,cache}
chmod 755 backend/storage/{uploads,sessions,cache}
echo "✅ Директории созданы."

# Запуск
echo ""
echo "🚀 Запуск приложения..."
if command -v docker-compose &> /dev/null; then
    docker-compose up -d --build
else
    docker compose up -d --build
fi

echo ""
echo "======================================"
echo "✅ Настройка завершена!"
echo ""
echo "🌐 Откройте в браузере: http://localhost:8000"
echo ""
echo "📋 Полезные команды:"
echo "  docker-compose logs -f ai-local  # Просмотр логов"
echo "  docker-compose restart ai-local  # Перезапуск"
echo "  docker-compose down              # Остановка"
echo ""
echo "🔧 Для изменения настроек отредактируйте .env и выполните:"
echo "  docker-compose up -d --build"