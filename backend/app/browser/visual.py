"""KaseVisualAnalyzer - reading a picture, honestly (§12, §13, §14, §33, §53).

The rule that shapes this whole module: **an image is not a source of precise
numbers.** Looking at a chart and concluding "the price went from 95 to 101" is
inventing data - the line's pixel position is not a quotation. So the analyzer
is built to answer qualitative questions ("is there a chart?", "what do the
labels say?", "is there a warning?", "is the trend up or down?") and its output
is stamped with a low confidence and the ``visual`` extraction method, which
the validator refuses for every price-like field (see
``validator.NEVER_FROM_VISUAL``).

Precise numbers come from the DOM, a table, or a tooltip. If those are
available, visual analysis is not run at all.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.ai.base import LLMClient
from app.ai.factory import get_llm_client
from app.browser.session import BrowserSession
from app.browser.types import VisualAnalysis
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = (
    "Ты анализируешь скриншот страницы Казахстанской фондовой биржи (KASE).\n"
    "СТРОГИЕ ПРАВИЛА:\n"
    "1. Описывай только то, что реально видно на изображении.\n"
    "2. НИКОГДА не оценивай числовые значения по положению линии графика. "
    "Если число не написано текстом — его нет.\n"
    "3. В visible_labels указывай ТОЛЬКО подписи, которые действительно "
    "напечатаны на изображении.\n"
    "4. Выводы о графике должны быть качественными: направление, всплески, "
    "структура, наличие подписей.\n"
    "5. Если изображение непонятно — скажи это и поставь низкий confidence.\n"
    "Ответь ТОЛЬКО валидным JSON без markdown-обертки, по схеме:\n"
    '{"description": str, "visible_labels": [str], "chart_present": bool, '
    '"table_present": bool, "warnings": [str], "qualitative_findings": [str], '
    '"confidence": float}'
)

#: Never let a visual conclusion be presented as certain.
MAX_VISUAL_CONFIDENCE = 0.6

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class KaseVisualAnalyzer:
    """Turns a screenshot into a qualitative, low-confidence description."""

    version = "1.0.0"

    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client or get_llm_client()

    @property
    def available(self) -> bool:
        return (
            settings.BROWSER_VISUAL_ANALYSIS_ENABLED
            and getattr(self._client, "supports_vision", False)
            and bool(settings.OPENAI_API_KEY)
        )

    async def analyze_file(
        self, path: str | Path, *, page_context: str = "", task: str = ""
    ) -> VisualAnalysis:
        file_path = Path(path)
        if not file_path.exists():
            return VisualAnalysis(
                available=False, reason=f"screenshot not found: {file_path}"
            )
        return await self.analyze_bytes(
            file_path.read_bytes(),
            page_context=page_context,
            task=task,
            screenshot_path=str(file_path),
        )

    async def analyze_bytes(
        self,
        image: bytes,
        *,
        page_context: str = "",
        task: str = "",
        screenshot_path: str | None = None,
    ) -> VisualAnalysis:
        if not self.available:
            return VisualAnalysis(
                available=False,
                screenshot_path=screenshot_path,
                reason=(
                    "Визуальный анализ выключен или модель без поддержки "
                    "изображений (BROWSER_VISUAL_ANALYSIS_ENABLED / OPENAI_API_KEY)."
                ),
            )
        prompt = (
            f"Контекст страницы: {page_context or 'не указан'}\n"
            f"Задача: {task or 'опиши, что изображено'}\n"
            "Верни JSON по схеме из системного сообщения."
        )
        response = await self._client.describe_image(
            image,
            prompt,
            system=SYSTEM_PROMPT,
            model=settings.BROWSER_VISION_MODEL or None,
        )
        if not response.ok:
            return VisualAnalysis(
                available=False,
                screenshot_path=screenshot_path,
                reason=response.error or "vision model returned nothing",
            )
        analysis = _parse(response.content)
        analysis.screenshot_path = screenshot_path
        analysis.confidence = min(analysis.confidence, MAX_VISUAL_CONFIDENCE)
        return analysis

    async def analyze_page_region(
        self,
        session: BrowserSession,
        *,
        target=None,
        name: str = "region",
        page_context: str = "",
        task: str = "",
    ) -> VisualAnalysis:
        """Screenshot an element (or the viewport) and describe it."""
        shot = await session.take_screenshot(target=target, name=name)
        if not shot.ok:
            return VisualAnalysis(available=False, reason=shot.error)
        return await self.analyze_file(shot.value, page_context=page_context, task=task)


def _parse(content: str) -> VisualAnalysis:
    match = _JSON_BLOCK.search(content)
    if match is None:
        # The model answered in prose. Keep it as a description rather than
        # discarding it, but do not pretend it was structured.
        return VisualAnalysis(
            description=content.strip()[:2000],
            confidence=0.2,
            warnings=["модель вернула не-JSON ответ"],
        )
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return VisualAnalysis(
            description=content.strip()[:2000],
            confidence=0.2,
            warnings=["JSON из ответа модели не разобран"],
        )

    def as_list(key: str) -> list[str]:
        value = data.get(key) or []
        if isinstance(value, str):
            return [value]
        return [str(item) for item in value][:30]

    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return VisualAnalysis(
        description=str(data.get("description") or "")[:4000],
        visible_labels=as_list("visible_labels"),
        chart_present=bool(data.get("chart_present")),
        table_present=bool(data.get("table_present")),
        warnings=as_list("warnings"),
        qualitative_findings=as_list("qualitative_findings"),
        confidence=max(0.0, min(1.0, confidence)),
    )


# -- charts (§13, §15) -------------------------------------------------------

#: Elements that usually *are* the chart. Used to aim a screenshot or a hover,
#: never to conclude that data exists.
CHART_SELECTORS = (
    "canvas",
    "svg.highcharts-root",
    "[class*='chart'] svg",
    "[class*='chart']",
    "[data-chart]",
)


async def find_chart_data_in_dom(session: BrowserSession) -> dict:
    """Look for real chart data before ever reaching for the pixels (§13).

    Checks, in order: SVG/canvas presence, an accessible data table linked to
    the chart, and common charting libraries' own series data. Anything found
    here is structured data with real numbers - vastly preferable to looking.
    """
    try:
        return await session.page.evaluate(
            """() => {
                const result = {
                    canvas: document.querySelectorAll('canvas').length,
                    svg: document.querySelectorAll('svg').length,
                    accessible_table: null,
                    series: null,
                    library: null,
                };
                // Charting libraries that expose their own series.
                if (window.Highcharts && Highcharts.charts) {
                    const charts = Highcharts.charts.filter(Boolean);
                    if (charts.length) {
                        result.library = 'highcharts';
                        result.series = charts[0].series.map((s) => ({
                            name: s.name,
                            points: (s.options.data || []).slice(0, 500),
                        }));
                    }
                }
                if (!result.series && window.Chart && Chart.instances) {
                    const list = Object.values(Chart.instances);
                    if (list.length) {
                        result.library = 'chartjs';
                        result.series = list[0].data.datasets.map((d) => ({
                            name: d.label,
                            points: (d.data || []).slice(0, 500),
                        }));
                    }
                }
                // An accessible description table beside the chart.
                const table = document.querySelector(
                    "[class*='chart'] table, figure table, .highcharts-data-table table"
                );
                if (table) result.accessible_table = table.innerText.slice(0, 5000);
                return result;
            }"""
        )
    except Exception as exc:
        logger.info("chart DOM probe failed: %s", exc)
        return {"canvas": 0, "svg": 0, "accessible_table": None, "series": None}


async def read_chart_tooltips(
    session: BrowserSession, *, samples: int = 5, selector: str | None = None
) -> list[dict]:
    """Hover across a chart and read whatever tooltip it shows (§15).

    Tooltip text is real text the site rendered, so values read here are
    ``tooltip`` method - trustworthy, unlike anything read off pixels. When no
    tooltip appears the result is simply empty; nothing is estimated.
    """
    box = None
    for candidate in ([selector] if selector else list(CHART_SELECTORS)):
        try:
            element = session.page.locator(candidate).first
            await element.wait_for(state="visible", timeout=2_500)
            box = await element.bounding_box()
            if box and box["width"] > 120 and box["height"] > 60:
                break
            box = None
        except Exception:
            continue
    if box is None:
        return []

    readings: list[dict] = []
    seen: set[str] = set()
    for index in range(max(1, samples)):
        fraction = (index + 0.5) / samples
        x = box["x"] + box["width"] * fraction
        y = box["y"] + box["height"] / 2
        await session.hover_at(x, y)
        await session.page.wait_for_timeout(350)
        text = await session.get_tooltip_text()
        if text and text not in seen:
            seen.add(text)
            readings.append({"x_fraction": round(fraction, 3), "tooltip_text": text})
    return readings
