# KASE Bond AI

Сервис анализа облигаций Казахстанской фондовой биржи (KASE).

**Главный принцип: вся сложность внутри системы, а не перед пользователем.**
На экране — доходность, доход после инфляции, надежность, ликвидность,
потенциал роста, общая оценка, калькулятор «если вложить X ₸» и ответ на вопрос
«почему такая оценка». YTM, duration, convexity и кредитный спред доступны, но
только в режиме «Подробно».

---

## Содержание

- [Быстрый старт](#быстрый-старт)
- [Переменные окружения](#переменные-окружения)
- [Тесты](#тесты)
- [Демо-режим](#демо-режим)
- [Подключение KASE](#подключение-kase)
- [Собственный ИИ](#собственный-ии)
- [Архитектура](#архитектура)
- [Правила работы с данными](#правила-работы-с-данными)

---

## Быстрый старт

### Docker (рекомендуется)

```bash
cp .env.example .env
docker compose up --build
```

После запуска:

| Компонент  | Адрес                          |
|------------|--------------------------------|
| Frontend   | http://localhost:3000          |
| Backend    | http://localhost:8000          |
| Local AI   | внутри Compose: `ai:8100`      |
| Swagger    | http://localhost:8000/docs     |
| PostgreSQL | localhost:5432                 |

Контейнер бэкенда сам дожидается PostgreSQL, применяет миграции
(`alembic upgrade head`) и, если `SEED_DEMO_DATA=true`, загружает
демонстрационный набор данных. Контейнер `ai` поднимает собственный локальный
inference-сервис; backend не стартует, пока его health-check не пройдет.

Проверить, что всё поднялось:

```bash
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/health/kase
```

### Локальный запуск без Docker

```bash
# 1. база
docker run -d --name kase-db -p 5432:5432 \
  -e POSTGRES_USER=kase -e POSTGRES_PASSWORD=kase -e POSTGRES_DB=kase_bond_ai \
  postgres:16-alpine

# 2. зависимости backend и локального AI
python -m venv .venv
.venv/Scripts/activate           # Windows
# source .venv/bin/activate      # Linux / macOS
pip install -r backend/requirements-dev.txt
pip install -r ai/requirements.txt

cp .env.example .env
export DATABASE_URL=postgresql+psycopg://kase:kase@localhost:5432/kase_bond_ai
alembic upgrade head
python scripts/seed_demo.py      # демо-данные, см. ниже

# В первом терминале — наш AI, во втором — backend.
uvicorn ai.inference.server:app --port 8100
uvicorn app.main:app --reload --app-dir backend

# 3. фронтенд
cd frontend
npm install
npm run dev
```

---

## Переменные окружения

Полный список с комментариями — в [`.env.example`](.env.example). Ключевые:

| Переменная            | Назначение                                                            |
|-----------------------|-----------------------------------------------------------------------|
| `APP_ENV`             | `development` / `staging` / `production` / `test`                     |
| `DATABASE_URL`        | строка подключения SQLAlchemy к PostgreSQL                            |
| `KASE_DATA_MODE`      | `auto` / `official_api` / `browser` / `website_structured` / `mock`   |
| `KASE_AI_DATA_MODE`   | `live` — AI сам обновляет факты с официального KASE; `snapshot` — офлайн |
| `KASE_API_KEY`        | ключ контрактного API KASE; нужен **только** для `official_api`        |
| `BROWSER_ENABLED`     | браузерный агент для публичного сайта kase.kz (ключ API не нужен)      |
| `KASE_API_URL`        | базовый URL API KASE                                                  |
| `OPENAI_API_KEY`      | ключ LLM-провайдера; пусто — ИИ отключается, объяснения даёт движок   |
| `AI_BASE_URL`         | любой OpenAI-совместимый эндпоинт                                     |
| `AI_MODEL`            | имя модели                                                            |
| `RUN_LIVE_KASE_TESTS` | разрешить тестам ходить в реальный KASE                               |
| `RUN_LIVE_BROWSER_TESTS` | разрешить браузерному тесту открыть настоящий kase.kz             |
| `SEED_DEMO_DATA`      | загрузить демо-данные при старте контейнера (игнорируется в проде)    |

Заполнение:

```bash
cp .env.example .env
# отредактируйте .env: как минимум DATABASE_URL и KASE_DATA_MODE
```

---

## Тесты

```bash
pip install -r backend/requirements-dev.txt
pytest
```

Тесты используют временную SQLite-базу (`tests/.test.db`) и не требуют
PostgreSQL. Покрыто:

- цена облигации, текущая доходность, YTM, duration, modified duration,
  convexity, pull-to-par, НКД, day-count конвенции;
- генерация графика выплат (в том числе дисконтные и плавающий купон);
- реальная доходность (формула Фишера) и многолетний реальный результат;
- границы 0–100 у всех оценок, разделение корпоративной и банковской моделей,
  влияние профиля риска;
- валидация API, права доступа к портфелю, маркировка демо-данных.

Тесты, ходящие в реальный KASE, помечены `@pytest.mark.live_kase` и по
умолчанию не запускаются:

```bash
RUN_LIVE_KASE_TESTS=true pytest -m live_kase
```

Проверка типов фронтенда:

```bash
cd frontend && npm run typecheck
```

---

## Демо-режим

Демо-данные существуют, чтобы можно было посмотреть продукт без договора с
биржей. Они **синтетические** и всегда помечены:

- каждая котировка сохраняется с `data_mode = "mock"`, источник — `source = "mock"`;
- `GET /api/v1/health/kase` возвращает `connected: false`, `is_mock: true` и
  текстовое предупреждение;
- фронтенд показывает жёлтый баннер на каждой странице;
- `Data Quality Score` для таких выпусков умножается на 0.5.

Загрузка:

```bash
python scripts/seed_demo.py
```

Скрипт откажется работать при `APP_ENV=production`.

---

## Подключение KASE

Проект **не содержит поддельного API KASE** и не утверждает, что биржа
подключена, пока это не подтверждено фактической проверкой.

Режимы (`KASE_DATA_MODE`):

| Режим          | Поведение                                                                    |
|----------------|------------------------------------------------------------------------------|
| `official_api` | только контрактный API KASE; без `KASE_API_KEY` приложение не стартует        |
| `browser`      | настоящий браузер на публичном сайте kase.kz; ключ API не нужен               |
| `website_structured` | чтение публичного HTML по HTTP без браузера (псевдоним: `website`)     |
| `auto`         | API (если есть ключ) → браузер → HTML → демо (демо только вне production)     |
| `mock`         | только демо-данные; в production запрещён                                     |

Что реализовано и что нужно доделать под конкретный контракт — подробно
описано в [`docs/kase-integration.md`](docs/kase-integration.md).
Браузерный агент (вкладки, таблицы, документы, скриншоты, ограничения) —
в [`docs/browser-agent.md`](docs/browser-agent.md).

Честная проверка подключения:

```bash
python scripts/check_kase.py       # код возврата 0 — ответил реальный источник
curl http://localhost:8000/api/v1/health/kase
```

Обновление данных:

```bash
python scripts/refresh.py            # полная синхронизация
python scripts/refresh.py --quotes   # только котировки и производные величины
```

---

## Собственный ИИ

Интеллект продукта — свой. Датасет, обучение, веса, промпты, инструменты,
retrieval и инференс живут в [`ai/`](ai/README.md). Закрытый внешний LLM API
не участвует в ответе ни на одном шаге и не служит резервным вариантом.

```
Frontend → KASE Bond Backend → наш сервис инференса (127.0.0.1:8100)
```

Запуск:

```bash
pip install -r ai/requirements.txt
python -m ai.datasets.build --version v0.1.0     # датасет из снимка KASE
python -m ai.retrieval.index --version v0.1.0    # индекс для retrieval
uvicorn ai.inference.server:app --port 8100      # сервис инференса
curl http://localhost:8100/health
```

Работает без GPU, без весов и без сети: рантайм по умолчанию — детерминированный
движок, который маршрутизирует вопрос в инструмент и излагает результат.
Обучение (Qwen3-8B + QLoRA) требует GPU-машины и на момент этой версии не
запускалось — см. [model card](models/kase-ai-v0.1/model_card.md).

Переменные:

| Переменная | Значение |
|---|---|
| `AI_PROVIDER` | `local` (по умолчанию) / `external` / `off` |
| `KASE_AI_URL` | адрес нашего сервиса инференса |
| `KASE_AI_RUNTIME` | `rules` / `vllm` / `llama_cpp` / `transformers` |

`AI_PROVIDER=external` подключает любой OpenAI-совместимый эндпоинт
(`AI_BASE_URL` + `AI_MODEL` + `OPENAI_API_KEY`). Этот режим существует только
для сравнительного прогона «наша модель против чужой» и пишет предупреждение в
лог; продуктовой конфигурацией он не является.

Если сервис инференса недоступен или `AI_ENABLED=false`, приложение продолжает
работать: объяснения оценок формирует детерминированный генератор
(`app/scoring/explain.py`), и в ответе указано `generated_by: "engine"`.

**Модель не считает финансовые показатели.** YTM, дюрацию, денежные потоки и
оценки считают инструменты; модель выбирает инструмент, получает готовые числа
и объясняет их.

Документация: [архитектура](docs/ai/architecture.md) ·
[датасет](docs/ai/dataset.md) · [обучение](docs/ai/training.md) ·
[оценка](docs/ai/evaluation.md) · [инференс](docs/ai/inference.md) ·
[model card](docs/ai/model-card.md)

---

## Архитектура

```
backend/app/
  api/           HTTP-слой: роуты, зависимости, схемы ответов
  core/          конфиг, ошибки, логирование, перечисления, кэш
  db/            declarative base, сессии
  models/        24 ORM-модели
  schemas/       pydantic-модели запросов и ответов
  calculations/  ЧИСТЫЙ расчетный движок (без БД, сети и LLM)
  scoring/       модель оценок: веса, компоненты, объяснения
  credit         корпоративная и банковская кредитные модели
  providers/     BondDataProvider и его реализации, провайдеры инфляции
  collectors/    загрузка данных провайдеров в БД
  repositories/  доступ к данным
  services/      бизнес-логика и сборка ответов
  ai/            LLM-абстракция, промпты, объяснения
  jobs/          фоновое обновление

frontend/
  app/           страницы Next.js (App Router)
  components/    UI-примитивы и layout
  features/      функциональные блоки: bonds, compare, portfolio, watchlist…
  services/      API-клиент
  hooks/         SWR-хуки
  stores/        zustand (режим Просто/Подробно, список сравнения)
  types/         типы ответов API
  utils/         форматирование (никаких финансовых расчетов)
```

Основные эндпоинты (префикс `/api/v1`):

```
GET  /health                              GET  /health/kase
GET  /bonds                               GET  /bonds/top
GET  /bonds/search                        GET  /bonds/{id}
GET  /bonds/{id}/metrics                  GET  /bonds/{id}/scores
GET  /bonds/{id}/cashflows                GET  /bonds/{id}/history
GET  /bonds/{id}/peers                    GET  /bonds/{id}/score-explanation
POST /bonds/{id}/calculate                POST /compare
GET  /settings                            PUT  /settings
GET  /portfolios                          POST /portfolios
GET  /portfolios/{id}                     POST /portfolios/{id}/positions
PUT  /portfolios/{id}/positions/{pid}     DELETE /portfolios/{id}/positions/{pid}
GET  /watchlist                           POST /watchlist
DELETE /watchlist/{id}                    GET  /meta/scoring-model
```

Подробнее об оценках — [`docs/scoring.md`](docs/scoring.md), о расчетах —
[`docs/calculations.md`](docs/calculations.md), об архитектуре —
[`docs/architecture.md`](docs/architecture.md).

---

## Правила работы с данными

1. **Нет данных — значит `NULL`.** Ноль никогда не подставляется вместо
   отсутствующего значения, потому что ноль — это тоже значение.
2. **У каждого числа есть происхождение**: `source`, `source_identifier`,
   `source_url`, `source_timestamp`, `fetched_at`, а у производных —
   `formula_version` и `model_version`.
3. **Демо-данные всегда видны как демо** и не могут попасть в production:
   фабрика провайдеров и коллектор отказываются работать при
   `APP_ENV=production`.
4. **Критические расчеты только на бэкенде.** Фронтенд форматирует числа, но
   не выводит новые.
5. **Веса модели оценки хранятся на бэкенде** и отдаются через
   `/meta/scoring-model`, чтобы показанная и объяснённая оценка не разошлись.

Сервис не является инвестиционной рекомендацией.
