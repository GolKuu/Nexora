"""BrowserService and BrowserSession - a real Chromium, driven politely.

``BrowserService`` owns the Playwright process and hands out sessions.
``BrowserSession`` owns one browser context (its tabs, its cookies, its
downloads) and exposes the primitive actions of §4. Everything above this
module - extractors, the tab explorer, the KASE agent - is written against
these primitives and never touches Playwright directly.

What this layer will not do (§21, §22, §54): it does not solve CAPTCHAs, does
not log in, does not bypass a paywall and does not touch anything that is not
served to an ordinary anonymous visitor. When it meets one of those walls it
stops and says so.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
import uuid
from pathlib import Path
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Download,
    Page,
    Playwright,
    async_playwright,
)

from app.browser.locators import LocatorTarget, find
from app.browser.pacing import backoff_delay, global_pacer
from app.browser.types import (
    ActionResult,
    BrowserStatus,
    NavigationEvent,
    PageSnapshot,
    utcnow,
)
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Text that means "a machine is being challenged". Detection only - never an
#: attempt to solve anything.
_CAPTCHA_MARKERS = (
    "captcha",
    "recaptcha",
    "hcaptcha",
    "cf-challenge",
    "checking your browser",
    "подтвердите, что вы не робот",
    "я не робот",
)

#: Text/URL that means "this needs an account".
_AUTH_MARKERS = (
    "/account/login",
    "/auth/login",
    "требуется авторизация",
    "необходимо войти",
    "please log in",
    "sign in to continue",
)

_BLOCK_MARKERS = (
    "the url you requested has been blocked",
    "access denied",
    "403 forbidden",
)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()


def _artifact_dir(sub: str) -> Path:
    path = Path(settings.BROWSER_ARTIFACT_DIR).expanduser() / sub
    path.mkdir(parents=True, exist_ok=True)
    return path


class BrowserUnavailableError(RuntimeError):
    """Playwright or its engine is not installed/launchable on this machine."""


class BrowserService:
    """Process-wide owner of the Playwright engine.

    One engine, many sessions (§23: no spawning a browser per request). The
    engine starts lazily on first use and is reused until ``aclose``.
    """

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()
        self._sessions: dict[str, "BrowserSession"] = {}

    @property
    def running(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    async def _launch(self) -> Browser:
        if self.running:
            return self._browser  # type: ignore[return-value]
        async with self._lock:
            if self.running:
                return self._browser  # type: ignore[return-value]
            try:
                self._playwright = await async_playwright().start()
                engine = getattr(self._playwright, settings.BROWSER_ENGINE)
                self._browser = await engine.launch(
                    headless=settings.BROWSER_HEADLESS,
                    args=(
                        ["--disable-blink-features=AutomationControlled"]
                        if settings.BROWSER_ENGINE == "chromium"
                        else []
                    ),
                )
            except Exception as exc:  # engine missing, sandbox, no display…
                await self._teardown()
                raise BrowserUnavailableError(
                    "Browser engine could not be started. Install it once with "
                    "`python -m playwright install chromium`. Original error: "
                    f"{exc}"
                ) from exc
            logger.info(
                "browser engine started: %s %s",
                settings.BROWSER_ENGINE,
                self._browser.version,
            )
            return self._browser

    async def new_session(self, *, label: str | None = None) -> "BrowserSession":
        if not settings.BROWSER_ENABLED:
            raise BrowserUnavailableError("BROWSER_ENABLED=false")
        browser = await self._launch()
        context = await browser.new_context(
            user_agent=settings.BROWSER_USER_AGENT,
            locale=settings.BROWSER_LOCALE,
            viewport={
                "width": settings.BROWSER_VIEWPORT_WIDTH,
                "height": settings.BROWSER_VIEWPORT_HEIGHT,
            },
            accept_downloads=True,
        )
        context.set_default_timeout(settings.BROWSER_ACTION_TIMEOUT_MS)
        context.set_default_navigation_timeout(settings.BROWSER_NAV_TIMEOUT_MS)
        session = BrowserSession(
            context, browser_version=browser.version, label=label
        )
        await session.start()
        self._sessions[session.id] = session
        return session

    async def close_session(self, session: "BrowserSession") -> None:
        self._sessions.pop(session.id, None)
        await session.aclose()

    async def _teardown(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    async def aclose(self) -> None:
        for session in list(self._sessions.values()):
            await session.aclose()
        self._sessions.clear()
        await self._teardown()

    def status(self) -> dict:
        return {
            "enabled": settings.BROWSER_ENABLED,
            "engine": settings.BROWSER_ENGINE,
            "headless": settings.BROWSER_HEADLESS,
            "running": self.running,
            "version": self._browser.version if self._browser else None,
            "open_sessions": len(self._sessions),
        }


class BrowserSession:
    """One browsing context: tabs, cookies, navigation log, downloads.

    Cookies are whatever the public site sets for a normal visitor - language,
    consent, an anonymous session (§20). No cookie is ever imported from
    elsewhere and no authentication state is created.
    """

    def __init__(
        self,
        context: BrowserContext,
        *,
        browser_version: str | None = None,
        label: str | None = None,
    ) -> None:
        self.id = uuid.uuid4().hex[:16]
        self.label = label
        self.context = context
        self.browser_version = browser_version
        self.navigation_log: list[NavigationEvent] = []
        self.network_log: list[dict] = []
        self.downloads: list[dict] = []
        self._pages: list[Page] = []
        self._active = 0
        self._action_number = 0
        self._last_status: int | None = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        page = await self.context.new_page()
        self._attach(page)
        self._pages = [page]
        self._active = 0

    def _attach(self, page: Page) -> None:
        page.on("response", self._on_response)
        page.on("download", lambda d: asyncio.create_task(self._on_download(d)))

    def _on_response(self, response) -> None:
        """Observe traffic for diagnostics only (§19).

        We record what the site itself asks for so that an officially used
        public endpoint can be *documented*. Nothing is replayed, no header or
        cookie is captured, and no non-public endpoint is probed.
        """
        try:
            if len(self.network_log) < 400:
                self.network_log.append(
                    {
                        "method": response.request.method,
                        "url": response.url,
                        "status": response.status,
                        "resource_type": response.request.resource_type,
                    }
                )
        except Exception:
            pass

    async def _on_download(self, download: Download) -> None:
        """Keep downloaded files instead of losing them (§36)."""
        try:
            target = _artifact_dir("downloads") / f"{self.id}_{download.suggested_filename}"
            await download.save_as(str(target))
            self.downloads.append(
                {
                    "document_name": download.suggested_filename,
                    "document_url": download.url,
                    "local_path": str(target),
                    "source_page": self.page.url,
                    "fetched_at": utcnow().isoformat(),
                }
            )
            logger.info("browser download saved: %s", target.name)
        except Exception as exc:
            logger.warning("download could not be saved: %s", exc)

    async def aclose(self) -> None:
        try:
            await self.context.close()
        except Exception:
            pass

    # -- plumbing ----------------------------------------------------------

    @property
    def page(self) -> Page:
        return self._pages[self._active]

    @property
    def tab_count(self) -> int:
        return len(self._pages)

    def _log(
        self,
        action: str,
        target: str | None,
        url_before: str | None,
        url_after: str | None,
        status: str,
        duration_ms: float,
        error: str | None = None,
    ) -> NavigationEvent:
        self._action_number += 1
        event = NavigationEvent(
            session_id=self.id,
            action_number=self._action_number,
            action=action,
            target=(target or "")[:500] or None,
            url_before=url_before,
            url_after=url_after,
            status=status,
            duration_ms=duration_ms,
            error=(error or "")[:1000] or None,
        )
        self.navigation_log.append(event)
        return event

    async def _run(
        self, action: str, target: str | None, coro_factory, *, expect_url_change: bool = False
    ) -> ActionResult:
        """Execute one primitive with timeout handling, logging and status."""
        url_before = self._safe_url()
        started = time.perf_counter()
        try:
            value = await coro_factory()
            status = BrowserStatus.OK.value
            error = None
        except asyncio.TimeoutError as exc:
            value, status, error = None, BrowserStatus.TIMEOUT.value, str(exc)
        except Exception as exc:
            message = str(exc)
            value = None
            status = (
                BrowserStatus.TIMEOUT.value
                if "Timeout" in message and "exceeded" in message
                else BrowserStatus.ERROR.value
            )
            error = message
        duration = (time.perf_counter() - started) * 1000
        url_after = self._safe_url()
        if status == BrowserStatus.OK.value and expect_url_change and url_after == url_before:
            # Not an error - SPA tabs legitimately keep the URL - but worth
            # recording so a "click did nothing" case is visible in the log.
            error = "url unchanged"
        self._log(action, target, url_before, url_after, status, duration, error)
        if status != BrowserStatus.OK.value:
            logger.info("browser action failed action=%s target=%s err=%s", action, target, error)
        return ActionResult(
            action=action,
            status=status,
            value=value,
            error=error if status != BrowserStatus.OK.value else None,
            duration_ms=duration,
            url=url_after,
        )

    def _safe_url(self) -> str | None:
        try:
            return self.page.url
        except Exception:
            return None

    # -- navigation (§4) ---------------------------------------------------

    async def open_url(
        self, url: str, *, wait_until: str = "domcontentloaded", retries: int | None = None
    ) -> ActionResult:
        attempts = retries if retries is not None else settings.BROWSER_MAX_RETRIES

        async def go() -> dict:
            last_error: Exception | None = None
            for attempt in range(1, attempts + 1):
                async with global_pacer.slot():
                    try:
                        response = await self.page.goto(
                            url,
                            wait_until=wait_until,  # type: ignore[arg-type]
                            timeout=settings.BROWSER_NAV_TIMEOUT_MS,
                        )
                    except Exception as exc:
                        last_error = exc
                        response = None
                if response is not None:
                    self._last_status = response.status
                    if response.status < 500:
                        return {"http_status": response.status, "url": self.page.url}
                    last_error = RuntimeError(f"HTTP {response.status}")
                if attempt < attempts:
                    await asyncio.sleep(backoff_delay(attempt))
            raise last_error or RuntimeError("navigation failed")

        return await self._run("browser.open", url, go)

    async def go_back(self) -> ActionResult:
        return await self._run("browser.back", None, lambda: self.page.go_back())

    async def go_forward(self) -> ActionResult:
        return await self._run("browser.forward", None, lambda: self.page.go_forward())

    async def reload(self) -> ActionResult:
        return await self._run("browser.reload", None, lambda: self.page.reload())

    async def wait_for_page(self, *, state: str = "networkidle", timeout_ms: int | None = None) -> ActionResult:
        """Wait for the SPA to settle (§18): hydration, then quiet network."""

        async def wait() -> str:
            try:
                await self.page.wait_for_load_state(
                    state,  # type: ignore[arg-type]
                    timeout=timeout_ms or settings.BROWSER_NAV_TIMEOUT_MS,
                )
            except Exception:
                # A site with polling never reaches networkidle. Fall back to
                # DOM readiness rather than declaring the page empty.
                await self.page.wait_for_load_state("domcontentloaded")
                return "domcontentloaded"
            return state

        return await self._run("browser.wait_for_page", state, wait)

    async def wait_for_selector(
        self, selector: str, *, timeout_ms: int | None = None, state: str = "visible"
    ) -> ActionResult:
        return await self._run(
            "browser.wait_for_selector",
            selector,
            lambda: self.page.wait_for_selector(
                selector,
                timeout=timeout_ms or settings.BROWSER_ACTION_TIMEOUT_MS,
                state=state,  # type: ignore[arg-type]
            ),
        )

    async def wait_for_content(
        self, *, min_chars: int = 400, timeout_ms: int | None = None
    ) -> ActionResult:
        """Wait until the body actually has content.

        An SPA's first HTML is empty; concluding "no data" from it would be a
        lie (§18). This waits for rendered text, not for markup to exist.
        """

        async def wait() -> int:
            deadline = time.monotonic() + (
                timeout_ms or settings.BROWSER_NAV_TIMEOUT_MS
            ) / 1000.0
            length = 0
            while time.monotonic() < deadline:
                length = await self.page.evaluate(
                    "() => (document.body && document.body.innerText || '').trim().length"
                )
                if length >= min_chars:
                    return length
                await self.page.wait_for_timeout(250)
            return length

        return await self._run("browser.wait_for_content", str(min_chars), wait)

    # -- interaction -------------------------------------------------------

    async def click(self, target: LocatorTarget | str, *, timeout_ms: int | None = None) -> ActionResult:
        spec = LocatorTarget(css=target) if isinstance(target, str) else target

        async def do() -> dict:
            match = await find(self.page, spec, timeout_ms=timeout_ms or 3_000)
            if match is None:
                raise LookupError(
                    f"element not found by role/text/label/attribute/structure/css: {spec.describe()}"
                )
            await match.locator.scroll_into_view_if_needed(timeout=3_000)
            await match.locator.click(timeout=timeout_ms or settings.BROWSER_ACTION_TIMEOUT_MS)
            return {"strategy": match.strategy}

        return await self._run("browser.click", spec.describe(), do)

    async def click_text(self, text: str, *, exact: bool = False) -> ActionResult:
        return await self.click(LocatorTarget(text=text, exact=exact))

    async def click_at(self, x: float, y: float) -> ActionResult:
        """Coordinate click - the last resort only (§42).

        The caller is expected to have taken a screenshot and identified the
        target first, and to verify the page changed afterwards.
        """
        return await self._run(
            "browser.click_at", f"({x:.0f},{y:.0f})", lambda: self.page.mouse.click(x, y)
        )

    async def hover(self, target: LocatorTarget | str) -> ActionResult:
        spec = LocatorTarget(css=target) if isinstance(target, str) else target

        async def do() -> dict:
            match = await find(self.page, spec, timeout_ms=3_000)
            if match is None:
                raise LookupError(f"element not found: {spec.describe()}")
            await match.locator.hover(timeout=settings.BROWSER_ACTION_TIMEOUT_MS)
            return {"strategy": match.strategy}

        return await self._run("browser.hover", spec.describe(), do)

    async def hover_at(self, x: float, y: float) -> ActionResult:
        return await self._run(
            "browser.hover_at", f"({x:.0f},{y:.0f})", lambda: self.page.mouse.move(x, y)
        )

    async def fill(self, target: LocatorTarget | str, value: str) -> ActionResult:
        """Type into a public form field. Never used for credentials (§54)."""
        spec = LocatorTarget(css=target) if isinstance(target, str) else target

        async def do() -> dict:
            match = await find(self.page, spec, timeout_ms=3_000)
            if match is None:
                raise LookupError(f"field not found: {spec.describe()}")
            await match.locator.fill(value, timeout=settings.BROWSER_ACTION_TIMEOUT_MS)
            return {"strategy": match.strategy}

        # The value is data the user searched for, not a secret; the log keeps
        # the field description only.
        return await self._run("browser.fill", spec.describe(), do)

    async def press(self, key: str) -> ActionResult:
        return await self._run("browser.press", key, lambda: self.page.keyboard.press(key))

    async def scroll(self, *, pixels: int = 800) -> ActionResult:
        return await self._run(
            "browser.scroll",
            str(pixels),
            lambda: self.page.evaluate("(px) => window.scrollBy(0, px)", pixels),
        )

    async def scroll_to_bottom(self) -> ActionResult:
        return await self._run(
            "browser.scroll",
            "bottom",
            lambda: self.page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)"),
        )

    async def scroll_to_element(self, target: LocatorTarget | str) -> ActionResult:
        spec = LocatorTarget(css=target) if isinstance(target, str) else target

        async def do() -> dict:
            match = await find(self.page, spec, timeout_ms=3_000)
            if match is None:
                raise LookupError(f"element not found: {spec.describe()}")
            await match.locator.scroll_into_view_if_needed(timeout=5_000)
            return {"strategy": match.strategy}

        return await self._run("browser.scroll_to_element", spec.describe(), do)

    # -- tabs --------------------------------------------------------------

    async def open_tab(self, url: str | None = None) -> ActionResult:
        async def do() -> int:
            page = await self.context.new_page()
            self._attach(page)
            self._pages.append(page)
            self._active = len(self._pages) - 1
            if url:
                async with global_pacer.slot():
                    await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=settings.BROWSER_NAV_TIMEOUT_MS,
                    )
            return self._active

        return await self._run("browser.tabs.open", url, do)

    async def switch_tab(self, index: int) -> ActionResult:
        async def do() -> int:
            if not 0 <= index < len(self._pages):
                raise IndexError(f"no tab {index} (open tabs: {len(self._pages)})")
            self._active = index
            await self._pages[index].bring_to_front()
            return index

        return await self._run("browser.tabs.switch", str(index), do)

    async def close_tab(self, index: int | None = None) -> ActionResult:
        async def do() -> int:
            target = self._active if index is None else index
            if not 0 <= target < len(self._pages):
                raise IndexError(f"no tab {target}")
            if len(self._pages) == 1:
                raise RuntimeError("refusing to close the last tab of a session")
            await self._pages[target].close()
            self._pages.pop(target)
            self._active = min(self._active, len(self._pages) - 1)
            return self._active

        return await self._run("browser.tabs.close", str(index), do)

    def tabs(self) -> list[dict]:
        out = []
        for i, page in enumerate(self._pages):
            try:
                url = page.url
            except Exception:
                url = None
            out.append({"index": i, "url": url, "active": i == self._active})
        return out

    # -- reading -----------------------------------------------------------

    async def get_current_url(self) -> str:
        return self.page.url

    async def get_page_title(self) -> str:
        try:
            return await self.page.title()
        except Exception:
            return ""

    async def get_visible_text(self) -> str:
        try:
            return await self.page.evaluate(
                "() => (document.body && document.body.innerText) || ''"
            )
        except Exception:
            return ""

    async def get_page_html(self) -> str:
        try:
            return await self.page.content()
        except Exception:
            return ""

    async def get_element_text(self, target: LocatorTarget | str) -> ActionResult:
        spec = LocatorTarget(css=target) if isinstance(target, str) else target

        async def do() -> str:
            match = await find(self.page, spec, timeout_ms=3_000)
            if match is None:
                raise LookupError(f"element not found: {spec.describe()}")
            return (await match.locator.inner_text()).strip()

        return await self._run("browser.extract_text", spec.describe(), do)

    async def find_text(self, needle: str) -> bool:
        text = await self.get_visible_text()
        return needle.lower() in text.lower()

    async def get_links(self) -> list[dict]:
        try:
            return await self.page.evaluate(
                """() => [...document.querySelectorAll('a[href]')].map(a => ({
                    text: (a.innerText || '').trim().slice(0, 200),
                    href: a.href,
                    title: a.getAttribute('title'),
                    download: a.getAttribute('download'),
                }))"""
            )
        except Exception:
            return []

    async def get_tooltip_text(self) -> str | None:
        """Whatever tooltip/popover is currently on screen (§15)."""
        try:
            return await self.page.evaluate(
                """() => {
                    const sel = "[role=tooltip], .tooltip, .highcharts-tooltip, " +
                                ".apexcharts-tooltip.active, .chart-tooltip, [class*='tooltip']";
                    for (const el of document.querySelectorAll(sel)) {
                        const rect = el.getBoundingClientRect();
                        const text = (el.innerText || '').trim();
                        if (text && rect.width > 0 && rect.height > 0) return text;
                    }
                    return null;
                }"""
            )
        except Exception:
            return None

    # -- screenshots -------------------------------------------------------

    async def take_screenshot(
        self, *, target: LocatorTarget | str | None = None, name: str | None = None
    ) -> ActionResult:
        """Capture the viewport, or one element when ``target`` is given (§11)."""

        async def do() -> str:
            directory = _artifact_dir("screenshots")
            filename = f"{self.id}_{self._action_number + 1}_{name or 'view'}.png"
            path = directory / re.sub(r"[^\w.\-]+", "_", filename)
            if target is None:
                await self.page.screenshot(path=str(path))
            else:
                spec = LocatorTarget(css=target) if isinstance(target, str) else target
                match = await find(self.page, spec, timeout_ms=3_000)
                if match is None:
                    raise LookupError(f"element not found for screenshot: {spec.describe()}")
                await match.locator.screenshot(path=str(path))
            _prune_screenshots(directory)
            return str(path)

        return await self._run("browser.screenshot", name, do)

    # -- guards (§21, §22) -------------------------------------------------

    async def detect_blocks(self) -> dict:
        """Report walls; never climb them."""
        text = (await self.get_visible_text()).lower()
        html = (await self.get_page_html()).lower()
        url = (self._safe_url() or "").lower()
        captcha = any(m in text or m in html for m in _CAPTCHA_MARKERS)
        requires_auth = any(m in url or m in text for m in _AUTH_MARKERS)
        blocked = any(m in text for m in _BLOCK_MARKERS)
        return {
            "browser_blocked_by_captcha": captcha,
            "requires_authentication": requires_auth,
            "blocked": blocked,
        }

    # -- language (§43) ----------------------------------------------------

    async def detect_language(self) -> str | None:
        try:
            lang = await self.page.evaluate(
                "() => document.documentElement.getAttribute('lang')"
            )
        except Exception:
            return None
        if lang:
            return lang.split("-")[0].lower()
        url = self._safe_url() or ""
        match = re.search(r"//[^/]+/(ru|kz|en)(/|$)", url)
        return match.group(1) if match else None

    async def ensure_language(self, wanted: str | None = None) -> ActionResult:
        """Switch to the preferred language using the site's own control."""
        wanted = (wanted or settings.KASE_LANGUAGE).lower()

        async def do() -> dict:
            current = await self.detect_language()
            if current == wanted:
                return {"language": current, "switched": False}
            result = await self.click(LocatorTarget(text=wanted.upper(), exact=True))
            if not result.ok:
                return {"language": current, "switched": False, "note": "control not found"}
            await self.page.wait_for_timeout(1_000)
            return {"language": await self.detect_language(), "switched": True}

        return await self._run("browser.language", wanted, do)

    # -- snapshot ----------------------------------------------------------

    async def snapshot(
        self, *, section: str | None = None, keep_html: bool = False
    ) -> PageSnapshot:
        html = await self.get_page_html()
        text = await self.get_visible_text()
        blocks = await self.detect_blocks()
        status = BrowserStatus.OK.value
        if blocks["browser_blocked_by_captcha"]:
            status = BrowserStatus.BLOCKED_BY_CAPTCHA.value
        elif blocks["requires_authentication"]:
            status = BrowserStatus.REQUIRES_AUTHENTICATION.value
        elif blocks["blocked"]:
            status = BrowserStatus.UNAVAILABLE.value
        return PageSnapshot(
            url=self._safe_url() or "",
            page_title=await self.get_page_title(),
            fetched_at=utcnow(),
            html_hash=sha256(html),
            visible_text=text,
            status=status,
            browser_version=self.browser_version,
            browser_session_id=self.id,
            html=html if keep_html else None,
            language=await self.detect_language(),
            http_status=self._last_status,
        )

    def navigation_log_dicts(self) -> list[dict]:
        return [e.as_dict() for e in self.navigation_log]

    def observed_endpoints(self) -> list[dict]:
        """Public endpoints the site itself called (§19), for documentation."""
        seen: dict[str, dict] = {}
        for entry in self.network_log:
            if entry["resource_type"] in {"document", "xhr", "fetch"}:
                seen.setdefault(entry["url"], entry)
        return list(seen.values())


def _prune_screenshots(directory: Path) -> None:
    """Keep the artefact directory bounded (§32: not infinite screenshots)."""
    limit = settings.BROWSER_MAX_STORED_SCREENSHOTS
    files = sorted(directory.glob("*.png"), key=lambda p: p.stat().st_mtime)
    for path in files[:-limit] if len(files) > limit else []:
        try:
            path.unlink()
        except OSError:
            pass


#: Shared instance. Sessions are cheap, engines are not.
browser_service = BrowserService()
