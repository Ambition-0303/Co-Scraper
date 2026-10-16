"""Co-Scraper: query-aware DOM pruning and reusable scraper synthesis."""

from .dom import extract_main_html, simplify_html
from .pipeline import run

__all__ = ["extract_main_html", "run", "simplify_html"]
__version__ = "0.1.0"
