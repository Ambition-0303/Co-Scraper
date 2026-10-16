"""Execution helpers for generated WebPageParser code."""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from .inference import PipelineError


def execute_generated_code(code: str, htmls: Sequence[str]) -> list[dict[str, Any]]:
    """Execute generated code and parse each supplied HTML document.

    This intentionally runs model-generated Python in the current process. Use a
    container or another isolation boundary when processing untrusted models.
    """
    if not code.strip():
        raise PipelineError("generated code is empty")

    namespace: dict[str, Any] = {"__name__": "coscraper_generated_code"}
    try:
        exec(code, namespace)
        parser_class = namespace.get("WebPageParser")
        if parser_class is None:
            raise PipelineError("generated code does not define WebPageParser")
        parser = parser_class()
        results = []
        for html in htmls:
            page_started = time.perf_counter()
            result = parser.parse(html)
            if not isinstance(result, dict):
                raise PipelineError("WebPageParser.parse must return a dictionary")
            results.append({
                "elapsed_seconds": round(time.perf_counter() - page_started, 3),
                "result": result,
            })
        return results
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError(f"generated code execution failed: {exc}") from exc
