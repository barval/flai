# Модели: загрузка и настройка

## Загрузка GGUF-моделей

Стартовая конфигурация поставляется без файлов моделей. Разместите их в каталоге моделей перед первым запуском.

### LLM-модели (мультимодальная, рассуждения, эмбеддинг)

```bash
mkdir -p services/llamacpp/models

# Multimodal model (chat/router/vision, always resident) — must be in subdirectory with mmproj
mkdir -p services/llamacpp/models/Qwen3VL-8B-Instruct-Q4_K_M
wget -O services/llamacpp/models/Qwen3VL-8B-Instruct-Q4_K_M/Qwen3VL-8B-Instruct-Q4_K_M.gguf \
  "https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/Qwen3VL-8B-Instruct-Q4_K_M.gguf"
wget -O services/llamacpp/models/Qwen3VL-8B-Instruct-Q4_K_M/mmproj-F16.gguf \
  "https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main/mmproj-Qwen3VL-8B-Instruct-F16.gguf"

# Reasoning model (complex tasks)
wget -O services/llamacpp/models/Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf \
  "https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/main/Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf"

# Embedding model (RAG)
wget -O services/llamacpp/models/bge-m3-Q8_0.gguf \
  "https://huggingface.co/gpustack/bge-m3-GGUF/resolve/main/bge-m3-Q8_0.gguf"
```

### Модели генерации изображений (Z_image_turbo)

```bash
mkdir -p services/sd_cpp/models/{diffusion_models,vae,text_encoders}

# Diffusion model
wget -O services/sd_cpp/models/diffusion_models/z_image_turbo-Q8_0.gguf \
  "https://huggingface.co/leejet/Z-Image-Turbo-GGUF/resolve/main/z_image_turbo-Q8_0.gguf"

# VAE
wget -O services/sd_cpp/models/vae/ae.safetensors \
  "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/vae/ae.safetensors"

# LLM text encoder (for SD, separate copy with Q4_K_M quantization)
wget -O services/sd_cpp/models/text_encoders/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
  "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
```

### Модели редактирования изображений (Flux.2 Klein 4B)

```bash
# Diffusion model for editing
wget -O services/sd_cpp/models/diffusion_models/flux-2-klein-4b-Q8_0.gguf \
  "https://huggingface.co/leejet/FLUX.2-klein-4B-GGUF/resolve/main/flux-2-klein-4b-Q8_0.gguf"

# VAE for editing
wget -O services/sd_cpp/models/vae/flux2_ae.safetensors \
  "https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/vae/flux2-vae.safetensors"
```

> **Примечание**: Начиная с v10.0 отдельной чат-модели нет. Единственная копия Qwen3-4B в проекте — текстовый кодировщик SD (`Qwen3-4B-Instruct-2507-Q4_K_M.gguf` в `services/sd_cpp/models/text_encoders/`), необходимый stable-diffusion.cpp для генерации и редактирования изображений.

> ⚠️ **Важно**: Мультимодальные модели **обязательно** должны размещаться в поддиректории с именем модели, а файл `mmproj-*.gguf` — внутри неё. Роутер llama.cpp автоматически обнаружит и загрузит проектор.

### Модели генерации видео (LTX-Video 2B)

```bash
# Create models directory
mkdir -p services/ltx_video/models

# Diffusion transformer + VAE checkpoint
wget -O services/ltx_video/models/ltxv-2b-0.9.8-distilled.safetensors \
  "https://huggingface.co/Lightricks/LTX-Video/resolve/main/ltxv-2b-0.9.8-distilled.safetensors"

# T5 text encoder (run the download script)
bash services/ltx_video/download-t5-encoder.sh
```

## Модели, лицензии и размеры


### LLM-модели (llama.cpp)

| Модель | Назначение | Лицензия | Примерный размер |
|--------|-----------|---------|--------------|
| **`Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf`** | Рассуждения (все уровни) | [Лицензия Qwen](https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF) | ~12 ГБ |
| **`Qwen3VL-8B-Instruct-Q4_K_M`** | Мультимодальность — чат/роутер/vision | [Лицензия Qwen](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF) | ~5,5 ГБ + `mmproj` ~1,1 ГБ |
| **`bge-m3-Q8_0`** | Эмбеддинги (RAG) | [Лицензия MIT](https://huggingface.co/gpustack/bge-m3-GGUF) | ~0,6 ГБ |

### Модели генерации изображений (stable-diffusion.cpp)

| Модель | Назначение | Лицензия | Примерный размер |
|--------|-----------|---------|--------------|
| **Z-Image-Turbo** (`z_image_turbo-Q8_0`) | Генерация изображений | [Apache 2.0](https://huggingface.co/leejet/Z-Image-Turbo-GGUF) | ~6,5 ГБ |
| **`ae.safetensors`** (VAE) | Вариационный автоэнкодер для Z-Image | [Apache 2.0](https://huggingface.co/Comfy-Org/z_image_turbo) | ~0,3 ГБ |
| **`Qwen3-4B-Instruct-2507-Q4_K_M.gguf`** | Текстовый кодировщик для Z-Image | [Лицензия Qwen](https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF) | ~2 ГБ |

### Модели редактирования изображений (stable-diffusion.cpp)

| Модель | Назначение | Лицензия | Примерный размер |
|--------|------------|----------|-----------------|
| **Flux.2 Klein 4B** (`flux-2-klein-4b-Q8_0`) | Редактирование изображений (смена цветов, удаление объектов, стилизация) | [Apache 2.0](https://huggingface.co/leejet/FLUX.2-klein-4B-GGUF) | ~5 ГБ |
| **`flux2_ae.safetensors`** | VAE для редактирования Flux.2 | [Лицензия Flux](https://huggingface.co/Comfy-Org/flux2-dev) | ~0,3 ГБ |

### Модели генерации видео

| Модель | Назначение | Лицензия | Примерный размер |
|--------|-----------|---------|--------------|
| **`ltxv-2b-0.9.8-distilled.safetensors`** | Диффузионный трансформер LTX-Video 2B + VAE | [Лицензия LTX-Video](https://huggingface.co/Lightricks/LTX-Video) | ~5,9 ГБ |
| **PixArt T5-XXL** (`text_encoder`) | Текстовый кодировщик T5 для LTX-Video | [Лицензия PixArt](https://huggingface.co/PixArt-alpha/PixArt-XL-2-1024-MS) | ~18 ГБ (диск, float32) |

### Модели долговременной памяти

| Модель | Назначение | Лицензия | Примерный размер |
|--------|------------|----------|-----------------|
| **`nomic-embed-text-v1.5`** | Эмбеддинги текста для SLM | [Apache 2.0](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5) | ~500 МБ |

### Морфологический анализ

| Пакет | Назначение | Лицензия |
|-------|-----------|---------|
| **`pymorphy3`** | Морфологический анализ русского языка для распознавания названий комнат камер (генерация падежных форм) | [Лицензия MIT](https://github.com/kmike/pymorphy3) |

### Голосовые модели

| Модель | Назначение | Лицензия | Примерный размер |
|--------|------------|----------|-----------------|
| **`en_US-ryan-medium`** | Английский TTS (мужской) | [BSD-3-Clause (Piper)](https://huggingface.co/rhasspy/piper-voices) | ~63 МБ |
| **`en_US-ljspeech-medium`** | Английский TTS (женский) | [BSD-3-Clause (Piper)](https://huggingface.co/rhasspy/piper-voices) | ~63 МБ |
| **`ru_RU-dmitri-medium`** | Русский TTS (мужской) | [BSD-3-Clause (Piper)](https://huggingface.co/rhasspy/piper-voices) | ~63 МБ |
| **`ru_RU-irina-medium`** | Русский TTS (женский) | [BSD-3-Clause (Piper)](https://huggingface.co/rhasspy/piper-voices) | ~63 МБ |
| **`Whisper medium`** | Распознавание речи | [MIT (OpenAI)](https://github.com/openai/whisper) | ~1,5 ГБ |

### Общий размер загружаемых данных (приблизительно)

| Конфигурация | Примерный объём |
|--------------|-----------------|
| Минимальный (`Qwen3VL-4B` + `Qwen3.6-35B-A3B` + `bge-m3`, уровень 8 ГБ) | ~16 ГБ |
| Только CPU (`Qwen3VL-4B` + `gpt-oss-20b-mxfp4` + `bge-m3`) | ~15 ГБ |
| Полный LLM-стек (`Qwen3VL-8B` + `Qwen3.6-35B-A3B` + `bge-m3`) | ~20 ГБ |
| + Генерация изображений | ~29 ГБ |
| + Редактирование изображений | ~32 ГБ |
| + Голос (TTS + Whisper) | ~35 ГБ |
| + Генерация видео (LTX-Video + T5-кодировщик) | ~59 ГБ *(T5-кодировщик ~18 ГБ на диске в float32)* |
| + Долговременная память (модель эмбеддингов SLM) | ~59,5 ГБ *(SLM добавляет ~500 МБ)* |

> **Примечание**: После загрузки моделей ПЛИИ работает полностью офлайн. Внешние скрипты и модули не загружаются во время работы.

---

## Настройка моделей в админ-панели

### Структура GGUF-моделей

llama.cpp работает в **режиме роутера** (`--models-dir`), динамически загружая модели из общего каталога:

```
services/llamacpp/models/
├── Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf             # Reasoning (all tiers)
├── bge-m3-Q8_0.gguf                            # Embedding
├── Qwen3VL-8B-Instruct-Q4_K_M/                 # Multimodal (subdirectory!) — chat/router/vision
│   ├── Qwen3VL-8B-Instruct-Q4_K_M.gguf
│   └── mmproj-F16.gguf                         # Vision projector
```

> ⚠️ **Мультимодальные модели требуют поддиректорию** с файлом проектора `mmproj-*.gguf` внутри. Сервер моделей автоматически обнаруживает и загружает его.

### Настройка моделей в админ-панели

1. Войдите как администратор и перейдите в `/admin` → вкладка **Модели**
2. Для каждого модуля (Мультимодальность, Рассуждения, Эмбеддинг):
   - Выберите GGUF-модель из выпадающего списка, настройте параметры, нажмите **Сохранить**

> 💡 **Смена модели эмбеддинга запускает автоматическую переиндексацию** всех документов.

### Скачивание, удаление и оффлайн-режим файлов моделей (Model Hub)

- **Скачивание:** вкладка **Hub** ищет модели на Hugging Face, показывает для каждого файла оценку занимаемой памяти GPU/ОЗУ и скачивает с прогрессом, докачкой и проверкой `sha256`. Прогресс находится в постоянной области активных загрузок вне результатов поиска, сохраняется при смене запроса и восстанавливается после перезагрузки той же вкладки. Показываемый общий размер округляется вверх до целых МБ с разделителями разрядов. Успешное скачивание создаёт маркер `.hubmeta`, в котором записаны служебные файлы модели (`mmproj`, `MTP`/draft-голова, текстовый кодировщик).
- **Модели и служебные файлы:** каждая многокомпонентная модель показывает суммарный размер всех своих шардов. Хвостовые шарды, `imatrix`, `MTP`/draft-головы и `FastMTP` — это служебные файлы, а не самостоятельные варианты модели. Для поддерживаемых `noMTP`-моделей с draft-головами `Q4_0` и `Q8_0` выбирается один вариант; по умолчанию выбран `Q8_0`.
- **Удаление:** у скачанных моделей появляется бейдж **✓ Загружена** с кнопкой **Удалить** — и во вкладке Hub, и в панели **«Загруженные модели»** во вкладке «Модели». Модель, скачанная через Hub, удаляется **вместе со своими служебными файлами**; модель, выбранная основной для какого-либо модуля, удалить нельзя.
- **Оффлайн:** когда Hugging Face недоступен, вкладка Hub показывает предупреждение и подсказывает ручной путь: положить файлы `.gguf` в `/models`, затем нажать **«Обновить список моделей»** во вкладке «Модели».
- ⚠️ **Файлы, размещённые вручную:** у `.gguf`, скопированного в `/models` вручную, **нет** маркера `.hubmeta`, поэтому его удаление убирает **только сам файл** — служебные файлы (например, `mmproj-*.gguf`, MTP-голова, текстовый кодировщик) не отслеживаются, и их нужно удалить вручную.

### Параметры моделей

| Параметр | Мультимодальность | Рассуждения | Эмбеддинг |
|----------|-------------------|-------------|-----------|
| Длина контекста | 32768 (авто-подбор: 24576 на 16 ГБ, 16384 на 8 ГБ, 8192 CPU) | 32768 на 24+ ГБ, 24576 на 16 ГБ / 16384 на 8 ГБ / 8192 CPU (авто-подбор) | 512 |
| Температура | 0.7 | 0.7 | – |
| Top P | 0.9 | 0.9 | – |
| Штраф за повтор | 1.1 | 1.15 | – |
| Таймаут (с) | 120 | 120 | 120 |

> **Примечание:** Классификация роутера всегда использует `temperature=0.1` (захардкожено) для детерминированной маршрутизации запросов, независимо от настроек админ-панели.

> **⚠️ Предупреждение — Штраф за повтор:** не задавайте слишком большое значение «Штраф за повтор» в админ-панели. Значения по умолчанию (1.1 / 1.15) намеренно консервативны; значения вроде 1.6 серьёзно деградируют модели рассуждений — проверено A/B-тестом на одном и том же промпте: 1.6 давал сожжённый контекст (59K символов убежавшего рассуждения + обрезанный ответ), пустой ответ и ответ не на том языке (4/4 неудачных генерации), тогда как 1.15 давал 4/4 чистых полных ответа. Симптомы завышенного штрафа: модель тратит весь контекст на `reasoning_content` и не отвечает, обрывается сразу после вводного предложения или уходит от запрошенного языка. Редкие повторы при длинной кодогенерации лучше закрывает встроенный детектор зацикливания (на стороне сервера), а не повышение этого параметра.

### Руководство по выбору моделей

| Компонент | По умолчанию | Рекомендуемая альтернатива | Примечания |
|-----------|-------------|---------------------------|------------|
| **Чат/роутер/vision** | `Qwen3VL-8B` `Q4_K_M` (~5,5 ГБ) | `Qwen3VL-4B` `Q4_K_M` (~2,5 ГБ, GPU 8 ГБ и режим только CPU) | Единая мультимодальная модель для всех трёх ролей; всегда в VRAM. Требует поддиректорию с `mmproj-*.gguf` |
| **Рассуждения** | `Qwen3.6-35B-A3B` `Q2_K_XL` (~12 ГБ) | `gpt-oss-20b-mxfp4` (~11,3 ГБ, по умолчанию в режиме только CPU) | MoE: ~3B активных параметров, ~106 т/с. Режим GPU: `Qwen3.6-35B` на всех уровнях (на 8 ГБ частичный вынос на CPU). Режим только CPU: нативный MXFP4, дружелюбен к процессору |
| **Эмбеддинг** | `bge-m3` `Q8_0` (~0,6 ГБ) | — | Единая модель для всех уровней |

> **Окна контекста:** значения по умолчанию авто-подбираются при развёртывании (`app/database.py:_autofit_context`) — 32768 для мультимодальной (24576 на 16 ГБ) и 32768/24576 для рассуждений на уровнях 24/16 ГБ (при текущей квантизации оба полностью помещаются в VRAM), 16384 на 8 ГБ, 8192 в режиме только CPU. Админ-панель следит за границами (512 … максимум архитектуры из GGUF) и — также при изменении только контекста — проверяет каждое сохранение на соответствие бюджету ОЗУ/VRAM, отклоняя значения, которые не помещаются, затем планирует фоновую dry-load новой конфигурации и автоматически откатывает изменение (восстанавливая `context_length`), если бэкенд не смог её загрузить.

---
