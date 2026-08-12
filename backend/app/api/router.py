from fastapi import APIRouter

from app.api.routes import (
    bonds,
    compare,
    health,
    meta,
    portfolios,
    settings as settings_routes,
    watchlist,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(meta.router, tags=["meta"])
api_router.include_router(bonds.router, prefix="/bonds", tags=["bonds"])
api_router.include_router(compare.router, tags=["compare"])
api_router.include_router(settings_routes.router, prefix="/settings", tags=["settings"])
api_router.include_router(portfolios.router, prefix="/portfolios", tags=["portfolios"])
api_router.include_router(watchlist.router, prefix="/watchlist", tags=["watchlist"])
