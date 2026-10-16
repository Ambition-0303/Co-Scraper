"""Playwright rendering helpers for URL-based seed pages."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

from .inference import PipelineError


@dataclass(frozen=True)
class RenderedPage:
    """A rendered page and basic navigation metadata."""

    source: str
    final_url: str
    title: str
    html: str


def is_url(value: str) -> bool:
    """Return whether an input is an HTTP(S) URL."""
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def render_pages(
    urls: Sequence[str],
    *,
    browser_timeout: float = 30_000.0,
    wait_until: str = "networkidle",
) -> list[RenderedPage]:
    """Render HTTP(S) pages with Chromium and return their serialized DOM."""
    if not urls:
        return []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PipelineError(
            "Playwright is unavailable; install it with the browser extra and "
            "download Chromium: pip install -e '.[browser]' && playwright install chromium"
        ) from exc

    rendered = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                for url in urls:
                    response = page.goto(url, timeout=browser_timeout, wait_until=wait_until)
                    if response is not None and not response.ok:
                        raise PipelineError(
                            f"failed to render {url}: HTTP {response.status} {response.status_text}"
                        )
                    rendered.append(
                        RenderedPage(
                            source=url,
                            final_url=page.url,
                            title=page.title(),
                            html=page.content(),
                        )
                    )
            finally:
                browser.close()
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(f"failed to render URLs with Playwright: {exc}") from exc
    return rendered
