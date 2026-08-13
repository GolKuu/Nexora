"""Resilient element location (§6, §41).

KASE changes its markup without notice, so no single CSS or XPath selector may
be load-bearing. Every lookup walks a ladder of strategies, strongest first:

    1. accessibility role + accessible name
    2. visible text (exact, then contained)
    3. label / aria-label / title / placeholder
    4. semantic and stable attributes (data-*, id, name)
    5. DOM structure (headings, table captions)
    6. CSS selector - last, and only as a hint

Whichever rung succeeds is reported back, so the navigation log records *how*
an element was found and a page redesign shows up as a strategy shift rather
than as a silent breakage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from playwright.async_api import Locator, Page

from app.core.logging import get_logger

logger = get_logger(__name__)

#: Roles worth trying for a clickable "tab-like" thing, in preference order.
CLICKABLE_ROLES = ("tab", "link", "button", "menuitem", "option")


@dataclass(slots=True)
class LocatorTarget:
    """What we are looking for, described in as many ways as the caller knows."""

    text: str | None = None
    role: str | None = None
    label: str | None = None
    test_id: str | None = None
    attribute: tuple[str, str] | None = None
    css: str | None = None
    exact: bool = False

    def describe(self) -> str:
        for name in ("text", "role", "label", "test_id", "css"):
            value = getattr(self, name)
            if value:
                return f"{name}={value}"
        if self.attribute:
            return f"attr[{self.attribute[0]}={self.attribute[1]}]"
        return "<unspecified>"


@dataclass(slots=True)
class LocatorMatch:
    locator: Locator
    strategy: str
    description: str


def _candidates(page: Page, target: LocatorTarget) -> list[tuple[str, Locator]]:
    """Build the ladder. Nothing is queried yet - these are lazy locators."""
    out: list[tuple[str, Locator]] = []

    # 1. accessibility role + name
    if target.text:
        roles = (target.role,) if target.role else CLICKABLE_ROLES
        for role in roles:
            out.append(
                (
                    f"role:{role}",
                    page.get_by_role(role, name=target.text, exact=target.exact),  # type: ignore[arg-type]
                )
            )
    elif target.role:
        out.append((f"role:{target.role}", page.get_by_role(target.role)))  # type: ignore[arg-type]

    # 2. visible text
    if target.text:
        out.append(("text:exact", page.get_by_text(target.text, exact=True)))
        if not target.exact:
            out.append(("text:contains", page.get_by_text(target.text, exact=False)))

    # 3. label-ish accessible names
    label = target.label or target.text
    if label:
        out.append(("label", page.get_by_label(label, exact=target.exact)))
        out.append(("title", page.get_by_title(label, exact=target.exact)))
        out.append(("placeholder", page.get_by_placeholder(label, exact=target.exact)))

    # 4. semantic / stable attributes
    if target.test_id:
        out.append(("test_id", page.get_by_test_id(target.test_id)))
    if target.attribute:
        name, value = target.attribute
        out.append((f"attr:{name}", page.locator(f"[{name}='{value}']")))
    if target.text:
        # Sites that render tabs as plain divs still tend to carry the label in
        # a data attribute; this catches those without hardcoding a class name.
        safe = target.text.replace("'", "\\'")
        out.append(("attr:data-*", page.locator(f"[data-tab='{safe}'], [data-name='{safe}']")))

    # 5. DOM structure: a heading with this text, then its section
    if target.text:
        out.append(
            (
                "structure:heading",
                page.locator("h1, h2, h3, h4, [role='heading']").filter(
                    has_text=target.text
                ),
            )
        )

    # 6. CSS, last
    if target.css:
        out.append(("css", page.locator(target.css)))

    return out


#: Never spend less than this on a single strategy, however many there are.
MIN_STRATEGY_TIMEOUT_MS = 200


async def find(
    page: Page, target: LocatorTarget, *, timeout_ms: int = 2_000
) -> LocatorMatch | None:
    """Walk the ladder and return the first strategy that sees the element.

    ``timeout_ms`` is the budget for the *whole* ladder, not for each rung.
    That distinction matters: with a dozen strategies, a per-rung timeout turns
    every miss into a half-minute stall, and a flow that has to try a few
    optional elements then blows its entire runtime budget on things that were
    never there.

    Returns ``None`` rather than raising: "not found" is an ordinary outcome
    that the caller reports honestly (§41), not an exception to swallow.
    """
    candidates = _candidates(page, target)
    if not candidates:
        return None
    per_strategy = max(MIN_STRATEGY_TIMEOUT_MS, timeout_ms // len(candidates))
    deadline = time.monotonic() + timeout_ms / 1000.0

    for strategy, locator in candidates:
        if time.monotonic() >= deadline:
            break
        try:
            candidate = locator.first
            await candidate.wait_for(state="visible", timeout=per_strategy)
        except Exception:
            continue
        logger.debug("located %s via %s", target.describe(), strategy)
        return LocatorMatch(candidate, strategy, target.describe())
    return None


async def find_all_text(page: Page, css_hints: list[str]) -> list[dict]:
    """Collect visible, clickable, text-bearing elements for tab discovery.

    Runs in the page so one round-trip returns everything; the CSS hints only
    *widen* the net, they are never the sole source of truth - anything with an
    ARIA tab role or a tab-ish class is picked up regardless.
    """
    hints = ", ".join(css_hints) if css_hints else "[role=tab]"
    return await page.evaluate(
        """(hints) => {
            const seen = new Set();
            const out = [];
            const nodes = document.querySelectorAll(
                "[role=tab], [role=tablist] a, [role=tablist] button, " + hints
            );
            for (const el of nodes) {
                const text = (el.innerText || el.textContent || "").trim();
                if (!text || text.length > 80) continue;
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 && rect.height === 0) continue;
                const style = window.getComputedStyle(el);
                if (style.display === "none" || style.visibility === "hidden") continue;
                const key = text.toLowerCase();
                if (seen.has(key)) continue;
                seen.add(key);
                out.push({
                    text,
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute("role"),
                    href: el.getAttribute("href"),
                    class_name: typeof el.className === "string" ? el.className : "",
                    active: /(^|[\\s_-])(active|selected|current)([\\s_-]|$)/i.test(
                        (typeof el.className === "string" ? el.className : "")
                    ) || el.getAttribute("aria-selected") === "true",
                });
            }
            return out;
        }""",
        hints,
    )
