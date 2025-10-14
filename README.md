## Краткая инструкция

### 1. Требования
- Python 3.12+
- (Опционально) NVIDIA GPU + CUDA для локальных моделей

### 2. Установка
```bash
git clone <repo_url>
cd functions_analysis

# Рекомендуется использовать uv
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
```

### 3. Настройка
1) Скопируйте пример и отредактируйте параметры:
```bash
cp env.example .env
```
2) Минимальные переменные в `.env`:
```bash
# Режимы: online | local
AI_MODE=local
AI_MODEL=gemma-3-27b-it
EMBEDDING_SERVER=http://localhost:7010
EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-mpnet-base-v2
USE_LOCAL_EMBEDDINGS=true

# Параметры модели
AI_TEMPERATURE=1.0
AI_MAX_TOKENS=4000

# Пути
INPUT_FOLDER=input_documents
OUTPUT_EXCEL=data/analysis_results.xlsx
OUTPUT_MARKDOWN_FOLDER=data/markdown_output
SPHERES_FILE=files/SPHERES.xlsx
UNIVERSAL_FUNCTIONS_JSON=files/universal_functions.json
```

Онлайн-режим:
```bash
AI_MODE=online
OPENAI_API_KEY=sk-...
AI_MODEL=gpt-4o-mini
```

### 4. Запуск
- Все этапы:
```bash
uv run python main.py
```
- Конкретные этапы:
```bash
uv run python main.py --stages 1 2 3 4 5 # --> Проранить 12345 этап
uv run python main.py --from 3 # --> Проранить с 3-го до конца
uv run python main.py --list # --> список этапов
```

### 6. Изменение параметров
- Все ключевые настройки находятся в `.env` (режимы, модели, температура, токены, пути).
- При старте все параметры печатаются в логах (API ключ маскируется).

### 7. Результаты
- Excel: путь из `OUTPUT_EXCEL`
- Markdown: папка из `OUTPUT_MARKDOWN_FOLDER`


