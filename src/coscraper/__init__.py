"""Co-Scraper: query-aware DOM pruning and reusable scraper synthesis."""

from .browser import render_pages
from .dom import extract_main_html, simplify_html
from .execution import execute_generated_code
from .pipeline import run

__all__ = [
    "execute_generated_code",
    "extract_main_html",
    "render_pages",
    "run",
    "simplify_html",
]
__version__ = "0.1.0"
