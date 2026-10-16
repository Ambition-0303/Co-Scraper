"""Command-line interface for Co-Scraper."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .browser import is_url, render_pages
from .inference import DEFAULT_MODEL
from .pipeline import run


def load_schema(value: str) -> dict:
    path = Path(value)
    text = path.read_text(encoding="utf-8") if path.is_file() else value
    schema = json.loads(text)
    if not isinstance(schema, dict):
        raise ValueError("schema must be a JSON object")
    return schema


def load_seed_inputs(
    inputs: list[str],
    *,
    browser_timeout: float,
    wait_until: str,
) -> tuple[list[str], list[str]]:
    """Load local HTML files and render HTTP(S) inputs with Playwright."""
    urls = [value for value in inputs if is_url(value)]
    rendered = (
        {page.source: page for page in render_pages(
            urls,
            browser_timeout=browser_timeout,
            wait_until=wait_until,
        )}
        if urls
        else {}
    )

    htmls = []
    sources = []
    for value in inputs:
        if is_url(value):
            page = rendered[value]
            htmls.append(page.html)
            sources.append(value)
            continue

        path = Path(value)
        if not path.is_file():
            raise FileNotFoundError(f"seed HTML file not found: {value}")
        htmls.append(path.read_text(encoding="utf-8", errors="replace"))
        sources.append(value)
    return htmls, sources


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate and execute a reusable WebPageParser from three seed pages."
    )
    parser.add_argument("query", help="Natural-language extraction query.")
    parser.add_argument("schema", help="JSON Schema file path or inline JSON object.")
    parser.add_argument(
        "inputs",
        nargs=3,
        help="Three seed HTML files or HTTP(S) URLs.",
    )
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", ""))
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--id-max-tokens", type=int, default=8192)
    parser.add_argument("--code-max-tokens", type=int, default=32768)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--json-mode", action="store_true")
    parser.add_argument(
        "--browser-timeout",
        type=float,
        default=30_000.0,
        help="Playwright navigation timeout in milliseconds",
    )
    parser.add_argument(
        "--wait-until",
        choices=["domcontentloaded", "load", "networkidle"],
        default="networkidle",
        help="Playwright navigation wait condition",
    )
    parser.add_argument(
        "--no-execute",
        action="store_true",
        help="Generate code without executing it on the seed pages",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/coscraper"))
    return parser.parse_args()


def cli() -> None:
    args = parse_args()
    htmls, sources = load_seed_inputs(
        args.inputs,
        browser_timeout=args.browser_timeout,
        wait_until=args.wait_until,
    )
    result = run(
        args.query,
        load_schema(args.schema),
        htmls,
        sources=sources,
        model=args.model,
        base_url=args.base_url,
        api_key=os.environ.get(args.api_key_env, ""),
        id_max_tokens=args.id_max_tokens,
        code_max_tokens=args.code_max_tokens,
        timeout=args.timeout,
        max_attempts=args.max_attempts,
        json_mode=args.json_mode,
        execute=not args.no_execute,
        output_dir=args.output_dir,
    )
    print(json.dumps({
        "output_files": result.get("outputs", {}),
        "useful_item_ids": [page["useful_item_ids"] for page in result["pages"]],
        "id_elapsed_seconds": result["id_elapsed_seconds"],
        "code_elapsed_seconds": result["code_elapsed_seconds"],
        "execution_elapsed_seconds": result["execution_elapsed_seconds"],
        "execution_results": result["execution_results"],
        "total_elapsed_seconds": result["total_elapsed_seconds"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    cli()
