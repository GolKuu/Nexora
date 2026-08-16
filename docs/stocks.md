# Акции KASE

Поддержка акций отделена от bond domain. Таблица `instruments` хранит общую
идентичность, `stocks` — профиль акции, а котировки, отчетные периоды,
дивиденды, метрики и scores имеют собственные таблицы. Ни один stock endpoint
не вызывает YTM, cashflow или duration расчеты облигаций.

## Официальные источники

- `GET https://kase.kz/api/instruments/securities/?sec_type=share` — каталог,
  ISIN, класс акции, валюта, сегмент, best bid/offer, last/close, оборот,
  число сделок, дата данных и класс ликвидности.
- `GET https://kase.kz/api/companies/fin-data/{issuer_code}/` — опубликованные
  KASE агрегаты отчетности: revenue, net income, assets, equity и liabilities.
- `GET https://kase.kz/api/companies/documents/?org_code={issuer_code}&language=ru` — официальный
  каталог документов эмитента. Только новые корпоративные PDF разбираются через `pypdf`;
  dividend amount сохраняется лишь при явной сумме на одну акцию в тексте.
- Публичные страницы `https://kase.kz/{lang}/investors/shares/{ticker}/`
  — проверяемая ссылка инструмента и browser fallback для документов, новостей,
  дивидендов и corporate actions.

Collector не содержит списка тикеров. Строки KASE Global не смешиваются с
локальной equity-вселенной, а исторические записи без актуального ticker
metadata отбрасываются. `excl_date` не используется как единственный признак
делистинга: KASE возвращает старую `excl_date` и для некоторых действующих
бумаг; текущий `finish_date` является безопасным признаком завершения.

## Расчеты и неопределенность

P/E, P/B, EV/EBITDA, ROE, ROA, margin, growth и dividend yield считаются
детерминированным Python-кодом. Для банка EV/EBITDA возвращает `null`.
Отсутствующее значение никогда не заменяется нулем. Scores нормализуют только
доступные компоненты, а их покрытие отражается в Data Quality и confidence.

Публичный `fin-data` сейчас не публикует EBITDA, cash flow, EPS и полноценный
debt breakdown для большинства эмитентов. Поэтому P/E, EV/EBITDA и FCF yield
часто остаются `null` до подтвержденного разбора отчетного документа. Публичный
каталог также дает только best bid/offer, а не глубину стакана. Калькулятор
предпочитает свежий ask и явно предупреждает при использовании last.

## API

- `GET /stocks`, `GET /stocks/search`, `/stocks/top`
- `POST /stocks/search` — естественный запрос преобразуется только в проверенные,
  явно возвращаемые фильтры; неоднозначные слова сопровождаются assumptions.
- `GET /stocks/{identifier}`, `/stocks/{identifier}/analysis`, `/stocks/{identifier}/peers`
- `GET /stocks/{identifier}/financial-changes` — текущий период против предыдущего
  и год к году без повторного анализа всей истории.
- `GET /stocks/{identifier}/history` — котировки, метрики, scores и дивиденды с provenance.
- `POST /stocks/recommend`, `/stocks/compare`
- `POST /stocks/{identifier}/investment-calculation`
- `POST /stocks/refresh`
- `POST /instruments/compare` — cross-asset comparison without mixing bond YTM and stock scenarios
- `POST /watchlist` accepts either `bond` or `stock`; stock deletion uses `?instrument_type=stock`
- `GET|POST|PUT|DELETE /alerts` — анонимные и пользовательские stock/bond alerts;
  для акций доступны цена, P/E, дивиденды, отчетность, изменение прибыли,
  изменение score и новости.

Stock Analyst получает только сформированную backend-карточку с проверенными
данными. Если локальная AI-модель не настроена, используется детерминированное
объяснение. Вопрос о гарантированном росте всегда получает policy-ответ без
прогноза цены.

Public KASE news obtained by the existing Browser Agent passes through strict
`StockActionIngestionService` validation. Only KASE-hosted URLs are accepted,
and a Dividend is created only when a positive per-share amount is explicit.
Dates absent from the publication remain `null`. The paid KASE Corporate Events
feed is not connected automatically.

Momentum describes accumulated `StockQuote` history only: price trend,
annualized volatility, and maximum drawdown. With insufficient observations
the values remain `null`; none of these metrics is a price forecast.

Portfolio API сохраняет раздельные stock/bond позиции и выплаты, а также
возвращает asset allocation, currency allocation и issuer concentration.
Для смешанных валют доля эмитента не вычисляется без подтвержденного FX-курса.
- `GET /instruments/search` — общий результат с типом «Акция»/«Облигация»

CLI: `python scripts/kase.py sync-kase-stocks`. Планировщик обновляет stock
catalog вместе с incremental catalog job.
