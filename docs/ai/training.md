# Обучение

## Статус

**Fine-tuning ещё не запускался.** Пайплайн, конфиги и валидация готовы;
датасет `v0.1.0` проверку проходит. В окружении разработки нет GPU и нет
torch (Python 3.14 — под него нет колёс torch), поэтому запуск требует
отдельной GPU-машины. Ни одна цифра «до/после дообучения» в документации не
придумана: там, где замера не было, стоит явное «не измерено».

Пока веса не обучены, продукт отвечает детерминированным движком `rules` —
это измеренный пол, с которым будет сравниваться первый fine-tune.

## Окружение

```bash
python -m venv .venv-train && . .venv-train/bin/activate   # Python 3.11 или 3.12
pip install torch --index-url https://download.pytorch.org/whl/cu124
pip install -r ai/requirements.txt -r ai/requirements-training.txt
```

Датасет, retrieval, бенчмарк и инференс намеренно не зависят от torch — они
работают на основном интерпретаторе репозитория. Второе окружение нужно
только для обучения.

## Полный цикл

```bash
# 1. датасет
python -m ai.datasets.build --version v0.1.0 --today 2026-08-14

# 2. проверка перед GPU-временем
python -m ai.training.validate_dataset --config ai/configs/train_8b.yaml

# 3. посмотреть, чему именно учим (torch не нужен)
python -m ai.training.prepare_dataset --config ai/configs/train_8b.yaml --inspect 2

# 4. обучение
python -m ai.training.train_lora --config ai/configs/train_8b.yaml

# 5. слияние адаптера в базу
python -m ai.training.merge_adapter --run kase-ai-8b-v0.1

# 6. замер
python -m ai.evaluation.evaluate --label kase-ai-8b-v0.1 --runtime transformers

# 7. релизные ворота
python -m ai.evaluation.compare_models --gate \
    --baseline rules-tools-rag --candidate kase-ai-8b-v0.1

# 8. в реестр
python -m ai.training.registry promote kase-ai-8b-v0.1 \
    --baseline rules-tools-rag --candidate kase-ai-8b-v0.1
```

Шаг 2 не формальность: контаминация golden-набором или сломанная цель
`tool_call` стоят копейки сейчас и восемь GPU-часов потом.

## Конфигурации

`ai/configs/train_3b.yaml`, `train_8b.yaml`, `train_14b.yaml`. Параметры:
модель, датасет, learning rate, эпохи, batch, gradient accumulation, длина
последовательности, точность, LoRA rank/alpha, интервал чекпоинтов,
пороги релизных ворот.

| | 3b (4B) | 8b | 14b |
|---|---|---|---|
| База | Qwen3-4B | **Qwen3-8B** | Qwen3-14B |
| LoRA r / α | 16 / 32 | 32 / 64 | 32 / 64 |
| Длина | 2048 | 4096 | 4096 |
| Эффективный batch | 32 | 32 | 32 |
| LR | 1.5e-4 | 1e-4 | 7e-5 |
| Эпох | 3 | 3 | 2 |
| VRAM (QLoRA) | ~10 ГБ | ~20 ГБ | ~40 ГБ |
| Назначение | дев, CI, CPU-fallback | **production MVP** | P1 |

Smoke-прогон, чтобы прогнать весь код обучения на маленькой карте:

```bash
python -m ai.training.train_lora --config ai/configs/train_3b.yaml --smoke
```

## LoRA / QLoRA

Первый этап — QLoRA: база в 4-битном NF4, обучаются только адаптеры
(q/k/v/o/gate/up/down). Причины прагматичные: домен узкий, полное дообучение
здесь не окупается, а адаптер на 200 МБ несравнимо легче версионировать и
откатывать, чем чекпоинт на 30 ГБ.

Полное дообучение поддержано тем же скриптом (`train_sft.py`, либо
`lora.enabled: false`). При переключении learning rate автоматически
понижается до 1e-5: оставленный от LoRA 1e-4 разрушает языковые способности
базы за первые сотни шагов.

## Маскирование промпта

Loss считается только по ответу ассистента; токены системного промпта,
контекста и результатов инструментов маскируются в `-100`. Без этого модель
учится дословно воспроизводить документы KASE и полезные нагрузки
инструментов — и это прямой путь к тому, чтобы цитировать устаревшие рыночные
данные из памяти вместо вызова инструмента.

## Согласованность шаблона

`prepare_dataset.assert_template_agreement` сверяет `apply_chat_template`
токенизатора с нашим `render_chatml` и падает при расхождении. Расхождение
здесь — классическая причина дообучения, которое хорошо считается на
бенчмарке и плохо ведёт себя в проде.

## Чекпоинты и воспроизводимость

Каждый запуск пишет `models/<run>/metadata.json`: run id, базовая модель,
версия датасета, версии промптов/инструментов/формул, хеш конфига,
git-коммит, seed, версии библиотек, платформа, гиперпараметры, GPU,
GPU-часы, число примеров и токенов, итоговый loss, размеры артефактов.

`save_total_limit` держит последние N чекпоинтов, `--resume` продолжает с
любого.

## Реестр моделей

```
models/
  kase-ai-v0.1/          baseline (движок rules), model_card.md + metadata.json
  kase-ai-8b-v0.1/       будет создан первым запуском обучения
    metadata.json
    adapter/
    merged/
    checkpoints/
```

```bash
python -m ai.training.registry list
python -m ai.training.registry show kase-ai-v0.1
python -m ai.training.registry verify-license qwen3-8b   # хеш файла лицензии
python -m ai.training.cost_report --all                  # §56
```

Статусы: `trained` → `evaluated` → `production`. В `production` модель
переводится только через прошедшие релизные ворота — «выглядит хорошо» этот
реестр выразить не умеет.

## Отчёт о стоимости (§56)

`cost_report` печатает то, что записал запуск: тип GPU, GPU-часы, время,
примеры, токены, эпохи, loss, размеры адаптера и merged. Отсутствующее поле
печатается как «не записано» — то же правило, что продукт применяет к
рыночным данным.

Сейчас: **обучение не запускалось**, поэтому все поля пусты.

## Квантизация (§55)

```bash
python -m ai.training.export_model --run kase-ai-8b-v0.1 --format gguf --quant Q4_K_M
python -m ai.training.export_model --run kase-ai-8b-v0.1 --format awq
```

Экспортёр пишет рядом `quantization-*.json` с пометкой `benchmarked: false` и
командой для замера. Квантизация не считается принятой, пока не измерена:
4-битная модель, взятая «потому что помещается», начинает ошибаться в
арифметике, и без замера это списывают на что угодно, кроме экспорта.

## Непрерывное улучшение (§63, §64)

```
продакшн-фидбек → var/ai-review-queue.jsonl → человек → исправленный пример
   → новая версия датасета → обучение → бенчмарк → релизные ворота
```

Автоматического дообучения на фидбеке нет. Ответ, помеченный «не полезно»,
часто оказывается корректным отказом, и обучение на сыром сигнале научило бы
модель перестать отказываться. Правила ревью — `ai/training/review.md`.
