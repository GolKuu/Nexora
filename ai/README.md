# KASE Bond AI — собственная AI-система

Интеллект продукта — наш. Датасет, обучение, веса, промпты, инструменты,
retrieval и инференс находятся здесь. Закрытый внешний LLM API не участвует в
ответе ни на одном шаге и не служит резервным вариантом.

```
ai/
  configs/      выбор базовой модели, конфиги обучения, инференса, retrieval
  datasets/     сбор, очистка, разбор документов, чанкование, сборка датасета
  tools/        12 инструментов: реестр, права, исполнение
  prompts/      версионированные системные промпты и шаблоны чата
  embeddings/   открытая мультиязычная модель эмбеддингов + офлайн-резерв
  retrieval/    индекс, хранилище, понимание запроса, сборка контекста, reranker
  training/     подготовка, валидация, LoRA/полное обучение, слияние, экспорт, реестр
  evaluation/   golden-набор, метрики, бенчмарк, сравнение моделей, рубрика
  inference/    рантаймы, цикл агента, безопасность, HTTP-сервис
models/         веса и model card (веса не в git)
data/ai/        версионированные датасеты и индекс
docs/ai/        архитектура, датасет, обучение, оценка, инференс, model card
```

## Быстрый старт

```bash
pip install -r ai/requirements.txt

# 1. датасет из реального снимка KASE
python -m ai.datasets.build --version v0.1.0 --today 2026-08-14

# 2. индекс для retrieval
python -m ai.retrieval.index --version v0.1.0

# 3. сервис инференса (работает без GPU и без весов)
uvicorn ai.inference.server:app --port 8100
curl http://localhost:8100/health

# 4. бенчмарк
python -m ai.evaluation.evaluate --label rules-tools-rag
python -m ai.evaluation.compare_models --matrix
```

Ничего из этого не требует torch, GPU или сети.

## Обучение

Нужна GPU-машина и второе окружение:

```bash
pip install -r ai/requirements.txt -r ai/requirements-training.txt
python -m ai.training.validate_dataset --config ai/configs/train_8b.yaml
python -m ai.training.train_lora        --config ai/configs/train_8b.yaml
python -m ai.training.merge_adapter     --run kase-ai-8b-v0.1
python -m ai.evaluation.evaluate        --label kase-ai-8b-v0.1 --runtime transformers
python -m ai.evaluation.compare_models  --gate --baseline rules-tools-rag --candidate kase-ai-8b-v0.1
```

## Текущее состояние

| | |
|---|---|
| Базовая модель для fine-tune | Qwen/Qwen3-8B, Apache-2.0 |
| Fine-tuning | **не запускался** — нет GPU и torch в этом окружении |
| Отвечает сейчас | детерминированный движок `rules` (измеренный пол бенчмарка) |
| Датасет | `v0.1.0`, 647 примеров, 19 типов задач, русский |
| Индекс | `v0.1.0`, 332 чанка (резервный эмбеддер) |
| Бенчмарк | 73 вопроса, 12 категорий; tool selection 89.0%, галлюцинации 4.1% |
| Model card | `models/kase-ai-v0.1/model_card.md` |

Полные детали и честный список несделанного — в model card и `docs/ai/`.

## Правила, которые здесь не обсуждаются

1. **Модель не считает.** YTM, дюрация, выпуклость, денежные потоки, прибыль и
   оценки — детерминированные инструменты. Модель выбирает инструмент и
   объясняет результат.
2. **Модель не придумывает данные.** Нет данных — так и говорит.
3. **Документы — это данные, а не инструкции.**
4. **AI не торгует и не меняет рыночные значения.** Таких инструментов нет.
5. **Никаких «гарантированно заработаете».** Проверяется на каждом ответе.
6. **Неуверенность не ведёт к чужому API.** Уточнить, поискать, вызвать
   инструмент или сказать «недостаточно данных».
