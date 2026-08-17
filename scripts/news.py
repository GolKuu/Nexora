"""Operate the incremental news pipeline without exposing it as a public write API."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"backend"))

from app.collectors.tengrinews import TengrinewsCollector
from app.db.session import SessionLocal
from app.models.news import NewsArticle
from app.services.event_dataset import export_event_dataset
from app.services.news_intelligence import NewsIntelligencePipeline
from app.services.news_queries import NewsQueryService
from sqlalchemy import func,select

async def collect(session):
    latest=session.scalar(select(func.max(NewsArticle.published_at)).where(NewsArticle.source=="tengrinews"))
    return await NewsIntelligencePipeline(session).collect(TengrinewsCollector(),since=latest)

def main():
    parser=argparse.ArgumentParser();parser.add_argument("action",choices=("collect","stats","export"));parser.add_argument("--output",default=str(ROOT/"datasets/events/event_training_dataset.jsonl"));args=parser.parse_args()
    with SessionLocal() as session:
        if args.action=="collect": result=asyncio.run(collect(session))
        elif args.action=="stats": result=NewsQueryService(session).statistics()
        else: result=export_event_dataset(session,args.output)
        print(result)

if __name__=="__main__": main()
