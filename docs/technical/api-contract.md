# API-контракт для frontend

Базовый префикс: `/api/v1`. Всё — JSON, UTF-8.
Интерактивная схема: `/docs`, машинная: `/openapi.json`.

## Соглашения, действующие везде

| Правило | Смысл |
|---|---|
| `null` ≠ `0` | `null` означает «данных нет». Ноль означает настоящий ноль. Не подставляйте `0` вместо `null` в UI — покажите «нет данных» |
| Деньги | В валюте выпуска (`currency`), без округления до целых |
| Проценты | Уже умножены на 100. `total_return_percent: 32.55` — это 32.55 % |
| Цены | Поля `*_price` в списках — процент от номинала (100 = номинал). В калькуляторе `unit_clean_price` / `unit_dirty_price` — **деньги за одну бумагу** |
| Даты | ISO 8601. Даты — `YYYY-MM-DD`, метки времени — с таймзоной |
| Свежесть | Любой рыночный ответ несет `data_mode`, `data_timestamp`, `source` |
| `data_mode` | `live` \| `delayed` \| `end_of_day` \| `cached` \| `mock` |
| `mock` | Синтетические данные. В production невозможен. Если увидели — покажите предупреждение из поля `warning` |
| Ошибки | `{"detail": "..."}`, коды `404` (не найдено), `422` (валидация), `502` (источник недоступен) |

Публичный API KASE отдает **итоги торговой сессии**, поэтому нормальный
`data_mode` в проде — `end_of_day`. Не подписывайте цифры как «в реальном
времени».

---

## `GET /health/kase`

Реальный сетевой запрос к источнику. `connected: true` появляется **только**
после фактического успешного ответа не-демо источника — конфигурация сама по
себе его никогда не выставляет.

```jsonc
{
  "connected": true,
  "mode": "public_api",          // public_api | official_api | website | cache
  "provider": "kase_public_api",
  "last_attempt": "2026-08-13T02:42:41.419449+00:00",
  "last_success": "2026-08-13T02:42:41.419452+00:00",
  "latency_ms": 260.15,
  "data_age_seconds": 119476.7,  // возраст самой свежей сохраненной котировки
  "error": null,
  "is_mock": false,
  "data_mode": "end_of_day",
  "detail": "139 instruments in the latest session results.",
  "warning": null
}
```

При недоступности источника: `connected: false`, `error` заполнен,
`last_success: null`. Ответ остается `200` — это статус-эндпоинт.

---

## `POST /bonds/{identifier}/investment-calculation`

Главный расчет продукта. `identifier` — тикер или ISIN.

### Запрос

```json
{
  "mode": "amount",
  "amount": 5000000,
  "currency": "KZT",
  "commission": { "type": "percent", "value": 0.1 },
  "inflation_enabled": true,
  "exit_mode": "maturity",
  "exit_date": null,
  "scenario": "base"
}
```

| Поле | Значения | По умолчанию |
|---|---|---|
| `amount` | > 0, до 1e13. Любая сумма: 50 000 или 250 000 000 | обязательно |
| `commission.type` | `percent` \| `fixed` \| `none` | `percent` |
| `commission.value` | для `percent` — проценты (0.1 = 0.1 %) | `0` |
| `inflation_enabled` | `false` → все `real_*` поля станут `null`, номинальные не изменятся | `true` |
| `exit_mode` | `maturity` \| `date` | `maturity` |
| `exit_date` | `YYYY-MM-DD`, обязателен при `exit_mode="date"` (иначе `422`) | `null` |
| `scenario` | `bad` \| `base` \| `good` | `base` |

### Ответ (проверенный, реальные данные)

```jsonc
{
  "bond_identifier": "MFOKb21",
  "currency": "KZT",
  "input_amount": 5000000.0,
  "quantity": 5157,

  "unit_clean_price": 960.0,          // деньги за бумагу, без НКД
  "unit_dirty_price": 968.44,         // деньги за бумагу, с НКД
  "accrued_interest_per_bond": 8.44,

  "principal_cost": 4950720.0,
  "accrued_interest_total": 43548.0,
  "commission": 4994.27,

  "total_purchase_cost": 4999262.27,
  "cash_remaining": 737.73,           // не вложено: покупка идет лотами
  "minimum_required_amount": 969.41,  // сколько нужно на 1 лот

  "coupon_income": 1469745.0,
  "principal_repayment": 5157000.0,   // ВОЗВРАТ НОМИНАЛА — ЭТО НЕ ПРИБЫЛЬ
  "estimated_price_return": 206280.0,

  "total_profit": 1627482.73,         // = total_cash_received − total_purchase_cost
  "total_cash_received": 6626745.0,

  "total_return_percent": 32.5545,
  "annualized_return_percent": 20.9673,

  "real_profit": 739894.2,
  "real_return_percent": 14.8001,
  "real_annualized_return_percent": 10.31,
  "inflation_rate_percent": 10.2,
  "inflation_source": "stat.gov.kz · 2026-07-31",

  "holding_period_years": 1.4082,
  "price_basis": "ask",               // ask | last | bid | null
  "scenario": "base",
  "exit_mode": "maturity",
  "exit_date": "2027-12-30",

  "cashflows": [
    { "date": "2026-09-30", "type": "coupon",
      "coupon_amount": 128925.0, "principal_amount": 0.0,
      "total_amount": 128925.0, "is_estimated": false }
  ],

  "liquidity_warning": null,
  "warnings": ["Купоны не реинвестируются…", "Налоги и комиссии…"],

  "data_timestamp": "2026-08-13T02:38:36.077594",
  "source": "kase_public_api",
  "source_url": "https://kase.kz/api/trade-results/bonds/",
  "data_mode": "end_of_day"
}
```

### Что frontend обязан показать

**1. Прибыль ≠ все выплаты.** `principal_repayment` — это возврат собственных
денег. Никогда не складывайте его с `total_profit` и не называйте доходом.
Правильная разбивка:

```
Вернется всего:      total_cash_received
  из них номинал:    principal_repayment
  купоны:            coupon_income
Ваша прибыль:        total_profit
```

**2. `price_basis`.** Если `"last"` или `"bid"` — в `warnings` лежит явное
предупреждение, что это не гарантированная цена покупки. Покажите его.

**3. `liquidity_warning`.** Отдельное поле, не смешанное с `warnings` —
показывайте заметно. Пример реального текста:

> «Ликвидность ограничена: сумма покупки примерно в 6924.6× больше дневного
> оборота по выпуску; в последней сессии прошло всего 1 сделки. Весь объем
> может не исполниться по одной цене…»

**4. Недостаточно средств.** Ответ остается `200`, но:

```jsonc
{ "quantity": 0, "minimum_required_amount": 968.44,
  "warnings": ["Недостаточно средств для покупки одной облигации. Минимально необходимая сумма — 968 KZT"] }
```

Не показывайте пустой ноль — покажите `minimum_required_amount`.

**5. `inflation_enabled: false`** обнуляет только `real_*` и
`inflation_*` в `null`. Номинальные значения не меняются.

**6. `is_estimated`** в потоке означает, что выплата восстановлена из
параметров выпуска (KASE не публикует график купонов отдельно).

---

## `POST /bonds/recommend`

Отбор и ранжирование выполняет backend. **LLM в ранжировании не участвует** —
он может только объяснить готовый список, опираясь на `reason_codes`.

### Запрос

```json
{
  "amount": 5000000,
  "currency": "KZT",
  "max_maturity_years": 3,
  "min_maturity_years": null,
  "profile": "balanced",
  "inflation_enabled": true,
  "limit": 5,
  "commission": { "type": "percent", "value": 0.1 }
}
```

`profile`: `conservative` | `balanced` | `aggressive` — меняет веса ранжирования
(веса хранятся на backend и версионируются, см. `ranking_version`).

### Ответ

```jsonc
{
  "items": [
    {
      "ticker": "MFLGb18",
      "isin": "KZ2P00014269",
      "issuer": "МФО ОнлайнКазФинанс",
      "currency": "KZT",
      "maturity_date": "2027-06-15",
      "years_to_maturity": 0.84,
      "coupon_rate_pct": 26.0,
      "ytm_pct": 33.95,
      "real_ytm_pct": 21.55,
      "credit_score": null,          // null = не хватило данных, не «плохо»
      "liquidity_score": 28.1,
      "growth_score": 41.2,
      "investment_score": 65.1,
      "hold_score": 58.4,
      "data_quality_score": 46.0,
      "reason_codes": ["thin_liquidity", "size_exceeds_typical_turnover"],
      "investment_calculation": { /* тот же объект, что выше */ },
      "data_timestamp": "2026-08-13T02:38:36.077594",
      "data_mode": "end_of_day"
    }
  ],
  "amount": 5000000.0,
  "currency": "KZT",
  "profile": "balanced",
  "candidates_considered": 47,
  "ranking_version": "rank-1.0.0/scoring-1.0.0",
  "warning": null
}
```

Из выдачи исключаются выпуски без цены, с `data_quality_score < 25` и те, на
которые введенной суммы не хватает даже на одну бумагу.

### `reason_codes` — словарь

| Код | Смысл |
|---|---|
| `positive_real_yield` | доходность выше инфляции |
| `real_yield_below_inflation` | доходность ниже инфляции |
| `strong_credit_profile` / `weak_credit_profile` | Credit Score ≥ 70 / < 45 |
| `liquid_issue` / `thin_liquidity` | Liquidity Score ≥ 65 / < 35 |
| `wide_spread_over_govt` | спред к ГЦБ > 3 п.п. |
| `secured`, `subordinated`, `callable_early_redemption_risk` | признаки выпуска из CFI |
| `size_exceeds_typical_turnover` | сумма велика относительно оборота |
| `priced_off_last_trade_not_ask` | нет активной заявки на продажу |
| `price_upside_to_par` | Growth Score ≥ 65 |

Список стабилен и предназначен для локализации на стороне frontend.

---

## `GET /bonds/top`

| Параметр | Значения |
|---|---|
| `sort` | `investment_score`, `credit_score`, `liquidity_score`, `growth_score`, `hold_score`, `trade_score`, `real_return` |
| `currency` | код валюты |
| `max_maturity_years` | > 0 |
| `min_ytm`, `min_real_ytm` | в процентах |
| `min_credit_score` | 0–100 |
| `category` | тип выпуска |
| `limit` | 1–50, по умолчанию 10 |

Неизвестное значение `sort` → `422` со списком допустимых, а не молчаливая
сортировка по умолчанию.

Выпуски, у которых поле сортировки `null`, **исключаются**, а не считаются
нулем: отсутствие оценки — не то же самое, что низкая оценка.

Ответ — `BondListResponse` (см. ниже).

## `GET /bonds/search?q=…`

Ищет по тикеру, ISIN и названию эмитента. **Точное совпадение тикера или ISIN
идет первым**, затем совпадения по началу строки, затем подстроки.
`limit` 1–50.

## `GET /bonds` — список

`bond_type`, `currency`, `max_years`, `limit` (1–200), `offset`.

### `BondListResponse`

```jsonc
{
  "items": [{
    "id": 12, "ticker": "MFOKb21", "isin": "KZ2C00011906",
    "name": "…", "issuer_name": "…", "currency": "KZT",
    "bond_type": "corporate", "maturity_date": "2027-12-30",
    "years_to_maturity": 1.41, "coupon_rate_pct": 21.0,
    "yield_pct": 22.22, "real_yield_pct": 10.91,
    "clean_price": 96.0,
    "investment_score": 57.4, "credit_score": null,
    "liquidity_score": 33.2, "growth_score": 40.1,
    "hold_score": 55.0, "trade_score": 44.2, "data_quality_score": 51.0,
    "data_mode": "end_of_day"
  }],
  "total": 1, "limit": 50, "offset": 0,
  "data_mode": "end_of_day", "warning": null
}
```

## `GET /bonds/{identifier}` — карточка

Возвращает `bond`, `simple`, `pro`, `scores`, `freshness`, `warning`.
`freshness` несет `source`, `source_url`, `source_timestamp`, `fetched_at`,
`data_mode` — frontend всегда знает возраст данных.

## `GET /bonds/{identifier}/cashflows`

```jsonc
[{ "payment_date": "2026-12-18", "period_start": "2026-06-18",
   "coupon_amount": 55.0, "principal_amount": 0.0, "total_amount": 55.0,
   "is_estimated": false, "is_final": false }]
```

Типы потоков в калькуляторе: `coupon`, `principal`, `coupon_and_principal`,
`sale` (продажа до погашения). `call` / `put` появятся только если график
досрочного погашения станет известен — KASE его не публикует.

## `POST /compare`

```json
{ "identifiers": ["MFOKb21", "MFLGb18"], "mode": "simple",
  "amount": 5000000, "inflation_enabled": true }
```

До **10** идентификаторов. Если передан `amount`, все выпуски считаются на
одну и ту же сумму.

---

## Что фронтенду нужно помнить про данные KASE

1. **Цены — итоги сессии, не реальное время.** `data_age_seconds` в
   `/health/kase` показывает реальный возраст.
2. **`ask` бывает пуст.** Тогда `price_basis` ≠ `"ask"` и есть предупреждение.
3. **Глубина стакана недоступна.** Для крупных сумм всегда сверяйтесь с
   `liquidity_warning`.
4. **Кредитные рейтинги агентств публично отсутствуют**, поэтому
   `credit_score` может быть `null`. Это «недостаточно данных», а не «плохо».
5. **График купонов расчетный** — см. `is_estimated`.

Полное обоснование каждого пункта — в
[`kase-sources.md`](kase-sources.md).
