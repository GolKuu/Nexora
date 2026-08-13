"""The internal browser command set (§39).

Every capability the agent - or an AI planner - is allowed to use is named
here, with a timeout, error handling and a log line. An AI may *plan* a
sequence of these commands; it can never reach past them into Playwright, and
nothing it returns is written to the database without passing the validator
first (§38).
"""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable

from app.browser.extractors.documents import extract_documents
from app.browser.extractors.tables import extract_tables
from app.browser.locators import LocatorTarget
from app.browser.session import BrowserSession
from app.browser.types import ActionResult, BrowserStatus
from app.core.logging import get_logger

logger = get_logger(__name__)


def _target(spec: Any) -> LocatorTarget:
    if isinstance(spec, LocatorTarget):
        return spec
    if isinstance(spec, str):
        return LocatorTarget(text=spec)
    if isinstance(spec, dict):
        attribute = spec.get("attribute")
        return LocatorTarget(
            text=spec.get("text"),
            role=spec.get("role"),
            label=spec.get("label"),
            test_id=spec.get("test_id"),
            attribute=tuple(attribute) if attribute else None,  # type: ignore[arg-type]
            css=spec.get("css"),
            exact=bool(spec.get("exact", False)),
        )
    raise TypeError(f"cannot interpret locator target: {spec!r}")


class BrowserToolbox:
    """Dispatches ``browser.<command>`` against one session."""

    def __init__(self, session: BrowserSession) -> None:
        self.session = session

    # -- the contract ------------------------------------------------------

    def _commands(self) -> dict[str, Callable[..., Awaitable[Any]]]:
        s = self.session
        return {
            "browser.open": lambda url, **kw: s.open_url(url, **kw),
            "browser.back": lambda: s.go_back(),
            "browser.forward": lambda: s.go_forward(),
            "browser.reload": lambda: s.reload(),
            "browser.wait": lambda **kw: s.wait_for_page(**kw),
            "browser.wait_for_content": lambda **kw: s.wait_for_content(**kw),
            "browser.wait_for_selector": lambda selector, **kw: s.wait_for_selector(selector, **kw),
            "browser.find": lambda target: s.get_element_text(_target(target)),
            "browser.find_text": lambda text: s.find_text(text),
            "browser.click": lambda target, **kw: s.click(_target(target), **kw),
            "browser.click_text": lambda text, **kw: s.click_text(text, **kw),
            "browser.click_at": lambda x, y: s.click_at(x, y),
            "browser.hover": lambda target: s.hover(_target(target)),
            "browser.hover_at": lambda x, y: s.hover_at(x, y),
            "browser.fill": lambda target, value: s.fill(_target(target), value),
            "browser.press": lambda key: s.press(key),
            "browser.scroll": lambda **kw: s.scroll(**kw),
            "browser.scroll_to_bottom": lambda: s.scroll_to_bottom(),
            "browser.scroll_to_element": lambda target: s.scroll_to_element(_target(target)),
            "browser.extract_text": lambda **kw: self._extract_text(**kw),
            "browser.extract_table": lambda **kw: self._extract_tables(**kw),
            "browser.get_links": lambda: s.get_links(),
            "browser.get_html": lambda: s.get_page_html(),
            "browser.get_title": lambda: s.get_page_title(),
            "browser.get_url": lambda: s.get_current_url(),
            "browser.get_tooltip": lambda: s.get_tooltip_text(),
            "browser.screenshot": lambda **kw: s.take_screenshot(**kw),
            "browser.tabs": lambda: s.tabs(),
            "browser.tabs.open": lambda url=None: s.open_tab(url),
            "browser.tabs.switch": lambda index: s.switch_tab(index),
            "browser.tabs.close": lambda index=None: s.close_tab(index),
            "browser.download": lambda target: self._download(target),
            "browser.documents": lambda **kw: self._documents(**kw),
            "browser.language": lambda language=None: s.ensure_language(language),
            "browser.detect_blocks": lambda: s.detect_blocks(),
            "browser.snapshot": lambda **kw: s.snapshot(**kw),
        }

    @property
    def command_names(self) -> list[str]:
        return sorted(self._commands())

    async def run(self, action: str, **kwargs) -> ActionResult:
        """Execute one command. Unknown commands are refused, not guessed."""
        command = self._commands().get(action)
        if command is None:
            return ActionResult(
                action=action,
                status=BrowserStatus.ERROR.value,
                error=f"unknown browser command: {action}",
            )
        try:
            result = command(**kwargs)
            value = await result if inspect.isawaitable(result) else result
        except TypeError as exc:
            return ActionResult(
                action=action,
                status=BrowserStatus.ERROR.value,
                error=f"bad arguments for {action}: {exc}",
            )
        except Exception as exc:
            logger.info("browser command failed action=%s error=%s", action, exc)
            return ActionResult(
                action=action, status=BrowserStatus.ERROR.value, error=str(exc)
            )
        # Session primitives already return ActionResult with their own log
        # entry; plain reads are wrapped here so callers see one shape.
        if isinstance(value, ActionResult):
            return value
        return ActionResult(
            action=action,
            status=BrowserStatus.OK.value,
            value=value,
            url=self.session._safe_url(),
        )

    async def run_plan(self, steps: list[dict]) -> list[ActionResult]:
        """Run a planned sequence, stopping at the first failure.

        ``steps`` look like ``{"action": "browser.click", "args": {...}}``.
        A planner (AI or otherwise) produces candidates; this executes them and
        returns exactly what happened.
        """
        results: list[ActionResult] = []
        for step in steps:
            action = step.get("action", "")
            result = await self.run(action, **(step.get("args") or {}))
            results.append(result)
            if not result.ok:
                break
        return results

    # -- composite helpers -------------------------------------------------

    async def _extract_text(self, *, target: Any = None, clean: bool = True) -> Any:
        from app.browser.extractors.text import KaseTextExtractor

        if target is not None:
            return await self.session.get_element_text(_target(target))
        raw = await self.session.get_visible_text()
        return KaseTextExtractor().extract(raw) if clean else {"raw_text": raw}

    async def _extract_tables(self, *, section: str | None = None, **kw) -> list[dict]:
        tables = await extract_tables(self.session, section=section, **kw)
        return [t.as_dict() for t in tables]

    async def _documents(self, *, section: str | None = None) -> list[dict]:
        docs = await extract_documents(self.session, section=section)
        return [d.as_dict() for d in docs]

    async def _download(self, target: Any) -> dict:
        """Click something that triggers a download and keep the file (§36)."""
        before = len(self.session.downloads)
        result = await self.session.click(_target(target))
        if not result.ok:
            return {"downloaded": False, "error": result.error}
        # Give the download handler a moment to save the file.
        await self.session.page.wait_for_timeout(3_000)
        new = self.session.downloads[before:]
        return {"downloaded": bool(new), "files": new}
