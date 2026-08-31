from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.collectors.news import NewsSourceItem, article_fingerprint, canonicalize_url
from app.collectors.tengrinews import TengrinewsCollector
from app.models.news import EventMarketReaction, MarketEvent, NewsArticle
from app.models.issuer import Issuer
from app.models.instrument import Instrument
from app.models.stock import Stock
from app.services.event_dataset import build_training_rows, validate_no_lookahead
from app.services.event_study import QuotePoint, abnormal_return, align_event_to_quotes
from app.services.historical_events import HistoricalEventMatcher
from app.services.news_entity_linker import NewsEntityLinker
from app.services.news_intelligence import EVENT_TAXONOMY, NewsIntelligencePipeline, calculate_surprise, classify_event

UTC=timezone.utc

def make_stock(session, suffix="NEWS"):
    issuer=Issuer(code=f"I{suffix}",name=f"News Company {suffix}",short_name=f"NewsCo {suffix}",country="KZ")
    session.add(issuer);session.flush();instrument=Instrument(ticker=f"N{suffix}",isin=f"KZ000000{suffix[:4]:0<4}",issuer_id=issuer.id,instrument_type="stock",currency="KZT")
    session.add(instrument);session.flush();stock=Stock(instrument_id=instrument.id,lot_size=1);session.add(stock);session.flush();return stock

def test_url_fingerprint_drops_tracking_parameters():
    clean=canonicalize_url("HTTPS://Example.COM/a/?utm_source=x&keep=1#fragment")
    assert clean=="https://example.com/a?keep=1"
    now=datetime(2026,1,1,tzinfo=UTC)
    assert article_fingerprint("x",clean,"Title",now)==article_fingerprint("x",clean,"Other",now)

def test_tengrinews_collector_extracts_only_bounded_metadata():
    xml="""<rss><channel><item><title>Компания объявила контракт</title><link>https://tengrinews.kz/x</link><pubDate>Mon, 17 Aug 2026 10:00:00 +0000</pubDate><category>Экономика</category><description><![CDATA[<b>Короткий текст</b>]]></description></item></channel></rss>"""
    rows=TengrinewsCollector(max_extract_chars=20).parse(xml)
    assert len(rows)==1 and rows[0].source=="tengrinews" and rows[0].short_text=="Короткий текст"

@pytest.mark.parametrize(("text","kind"),[("Компания объявила дивиденды","dividend"),("Apple представила новую линейку iPhone","product_launch"),("Нацбанк изменил процентную ставку","interest_rate"),("неопределённое сообщение","other")])
def test_event_classification(text,kind): assert classify_event(text)==kind


# Russian keyword stems used to be matched as bare substrings, so any headline
# containing "риск", "выпуск" or "поиск" was filed as a lawsuit, "поставка" as
# an interest-rate move, and "прибыл" (arrived) as a profit report.
@pytest.mark.parametrize("text,kind",[
    ("Токаев прибыл на стадион","other"),
    ("Чистая прибыль выросла на 20%","profit"),
    ("Поставка оборудования завершена","other"),
    ("Национальный банк повысил базовую ставку","interest_rate"),
    ("Суд удовлетворил иск к эмитенту","lawsuit"),
])
def test_stems_match_word_starts_not_substrings(text,kind):
    assert classify_event(text)==kind


def test_arrival_headline_is_not_scored_as_positive_sentiment():
    from app.services.news_intelligence import language_sentiment
    assert language_sentiment("Токаев прибыл на стадион") == 0.0

def test_taxonomy_is_complete():
    for value in ("earnings","guidance","M&A","default","buyback","inflation","geopolitics","other"): assert value in EVENT_TAXONOMY

def test_surprise_never_invents_consensus():
    assert calculate_surprise(1.2,None) is None
    assert calculate_surprise(1.2,1.0)==pytest.approx(.2)

def _daily(start:datetime,prices:list[float],volumes:list[float]|None=None):
    volumes=volumes or [100]*len(prices)
    return [QuotePoint(start+timedelta(days=i),price,volumes[i]) for i,price in enumerate(prices)]

def test_market_closed_uses_next_trading_session_and_previous_price():
    friday=datetime(2026,8,14,15,tzinfo=UTC); monday=datetime(2026,8,17,15,tzinfo=UTC); tuesday=datetime(2026,8,18,15,tzinfo=UTC)
    result=align_event_to_quotes(datetime(2026,8,15,10,tzinfo=UTC),[QuotePoint(friday,100,100),QuotePoint(monday,102,170),QuotePoint(tuesday,103,110)])
    assert result["price_before"]==100 and result["return_1d"]==pytest.approx(.02)

def test_intraday_alignment_return_and_benchmark_adjustment():
    start=datetime(2026,8,17,9,tzinfo=UTC)
    points=[QuotePoint(start,100),QuotePoint(start+timedelta(minutes=35),101),QuotePoint(start+timedelta(hours=2),102),QuotePoint(start+timedelta(days=1),103)]
    result=align_event_to_quotes(start+timedelta(minutes=1),points)
    assert result["return_30m"]==pytest.approx(.01)
    assert abnormal_return(.024,.009)==pytest.approx(.015)

def test_volume_uses_rolling_baseline():
    start=datetime(2026,7,1,15,tzinfo=UTC); points=_daily(start,[100+i for i in range(23)],[100]*21+[170,100])
    result=align_event_to_quotes(start+timedelta(days=20,hours=1),points)
    assert result["volume_ratio"]==pytest.approx(1.7)

def test_alias_linking_supports_ticker_and_legal_name(session):
    stock=make_stock(session,"LINK"); linker=NewsEntityLinker(session)
    by_ticker=linker.link(f"Новости по {stock.instrument.ticker}")
    by_name=linker.link(stock.instrument.issuer.name)
    assert any(x.instrument_id==stock.instrument_id for x in by_ticker)
    assert any(x.issuer_id==stock.instrument.issuer_id for x in by_name)

def test_incremental_dedup_and_cross_source_clustering(session):
    stock=make_stock(session,"PIPE"); now=datetime.now(UTC)
    title=f"{stock.instrument.ticker} объявила новый крупный контракт"
    pipeline=NewsIntelligencePipeline(session)
    first,created=pipeline.ingest(NewsSourceItem("kase","https://kase.kz/n/unique-a",title,now,short_text=title,source_confidence=.99)); assert created
    events=pipeline.process_article(first); assert events and events[0].instrument_id==stock.instrument_id
    same,created=pipeline.ingest(NewsSourceItem("kase","https://kase.kz/n/unique-a?utm_source=x",title,now,short_text=title)); assert not created and same.id==first.id
    reprint,created=pipeline.ingest(NewsSourceItem("tengrinews","https://tengrinews.kz/unique-b",title,now+timedelta(minutes=2),short_text=title)); assert created
    assert pipeline.process_article(reprint)==[]
    assert session.scalar(select(func.count(MarketEvent.id)).where(MarketEvent.news_id.in_((first.id,reprint.id))))==len(events)

def test_historical_matcher_hides_small_sample(seeded,session):
    article=NewsArticle(source="test",source_url="https://x/1",canonical_url="https://x/1",title="x",published_at=datetime.now(UTC),fetched_at=datetime.now(UTC),content_hash="a"*64,fingerprint="b"*64,source_confidence=1,is_processed=True)
    session.add(article);session.flush(); event=MarketEvent(news_id=article.id,event_type="other",event_timestamp=datetime.now(UTC),importance=.5,source_confidence=1,analysis_confidence=1,relevance=1)
    session.add(event);session.flush(); result=HistoricalEventMatcher(session,minimum_sample_size=5).match(event)
    assert result["sufficient_sample"] is False and result["positive_reaction_rate"] is None

def test_dataset_features_do_not_contain_future_labels(seeded,session,tmp_path):
    rows=build_training_rows(session); validate_no_lookahead(rows)
    for row in rows: assert "return_1d" not in row["features"] and "labels" in row
