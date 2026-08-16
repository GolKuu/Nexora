"""Metadata-only observer for requests initiated by the public KASE page."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from app.browser.session import BrowserSession
from app.core.config import settings


class KaseNetworkObserver:
    """Expose safe request metadata without headers, cookies, tokens or bodies."""

    def __init__(self, session: BrowserSession) -> None:
        self.session = session

    @staticmethod
    def _official(url: str) -> bool:
        source_host = (urlsplit(settings.KASE_WEBSITE_URL).hostname or "").lower()
        host = (urlsplit(url).hostname or "").lower()
        return bool(source_host and (host == source_host or host.endswith("." + source_host)))

    def observed_endpoints(self) -> list[dict]:
        seen: dict[str, dict] = {}
        for entry in self.session.network_log:
            if entry.get("resource_type") not in {"document", "xhr", "fetch"}:
                continue
            url = str(entry.get("url") or "")
            if not self._official(url):
                continue
            parts = urlsplit(url)
            safe_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
            seen.setdefault(safe_url, {
                "method": entry.get("method"),
                "url": safe_url,
                "status": entry.get("status"),
                "content_type": entry.get("content_type"),
                "resource_type": entry.get("resource_type"),
                "auth_required": None,
                "license_uncertainty": True,
            })
        return list(seen.values())
