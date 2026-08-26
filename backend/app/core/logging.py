"""Logging setup."""

from __future__ import annotations

import logging
import sys

from app.core.config import settings

_CONFIGURED = False


def setup_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    # A Windows console defaults to a legacy codepage, so a log line carrying a
    # character it cannot encode (a Playwright report saying "2 x waiting", KASE
    # text in Russian) makes the handler itself raise. The message is lost and a
    # UnicodeEncodeError traceback is printed in its place, which reads like a
    # crawl failure while the crawl is in fact fine. Reconfiguring the stream to
    # UTF-8 with replacement keeps the line readable instead.
    stream = sys.stdout
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # a stream that cannot be reconfigured
            pass
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s :: %(message)s")
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.LOG_LEVEL.upper())
    logging.getLogger("uvicorn.access").setLevel("WARNING")
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
