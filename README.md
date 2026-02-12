# BarvalAILocalSite

ИИ Локальный - сайт для работы с локальным ИИ    

Он осуществляет:  
- чат с локальной ИИ-моделью (Ollama, используются модели - qwen3-vl:8b-instruct-q4_K_M, gpt-oss-20b)  
- создание изображений по описанию (Automatic1111, модель - cyberrealisticXL_v80.safetensors)  
- анализ изображений и файлов .png, .jpg, .jpeg  
- анализ текста из файлов .txt, .pdf  
- анализ голосовых сообщений   
- воспроизведение голосовых сообщений  
- домашнее видеонаблюдение  

## Требования
- виртуальная машина или физический сервер с установленными Docker и Docker-compose  
- доступ в Интернет (для скачивания моделей), либо локально размещённые Docker-образы и файлы моделей  
- доступ к сервису Ollama (модели - qwen3-vl:8b-instruct-q4_K_M, gpt-oss-20b)  
- доступ к сервису Automatic1111 (cyberrealisticXL_v80.safetensors)  
- доступ к сервису OpenAI Whisper  
- доступ к сервису room-snapshots-api  (опционально для домашнего видеонаблюдения)
- заполненные файлы:
     **.env** - системные параметры  
     **users.list** - список данных пользователей (e-mail, пароль)

## Запуск с Ollama
1. Убедитесь, что Ollama запущена и доступна:
```bash
curl http://localhost:11434/api/tags
```
2. Скачайте необходимые модели:  
```bash
ollama pull qwen3-vl:8b-instruct-q4_K_M
ollama pull gpt-oss-20b
```
3. Скопируйте .env.example в .env и отредактируйте при необходимости:
```bash
cp .env.example .env
```
4. Запустите приложение:
```bash
docker-compose up --build    
```
5. Откройте http://localhost:5000 и войдите с учетными данными из users.list  
  
