"""Command-line interface for Co-Scraper."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .inference import DEFAULT_MODEL
from .pipeline import run


def load_schema(value: str) -> dict:
    path = Path(value)
    text = path.read_text(encoding="utf-8") if path.is_file() else value
    schema = json.loads(text)
    if not isinstance(schema, dict):
        raise ValueError("schema must be a JSON object")
    return schema


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a reusable WebPageParser from three seed pages."
    )
    parser.add_argument("query", help="Natural-language extraction query.")
    parser.add_argument("schema", help="JSON Schema file path or inline JSON object.")
    parser.add_argument("html_files", nargs=3, help="Three seed HTML files.")
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", ""))
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--id-max-tokens", type=int, default=8192)
    parser.add_argument("--code-max-tokens", type=int, default=32768)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--json-mode", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/coscraper"))
    return parser.parse_args()


def cli() -> None:
    args = parse_args()
    htmls = [
        Path(path).read_text(encoding="utf-8", errors="replace")
        for path in args.html_files
    ]
    result = run(
        args.query,
        load_schema(args.schema),
        htmls,
        model=args.model,
        base_url=args.base_url,
        api_key=os.environ.get(args.api_key_env, ""),
        id_max_tokens=args.id_max_tokens,
        code_max_tokens=args.code_max_tokens,
        timeout=args.timeout,
        max_attempts=args.max_attempts,
        json_mode=args.json_mode,
        output_dir=args.output_dir,
    )
    print(json.dumps({
        "output_files": result.get("outputs", {}),
        "useful_item_ids": [page["useful_item_ids"] for page in result["pages"]],
        "id_elapsed_seconds": result["id_elapsed_seconds"],
        "code_elapsed_seconds": result["code_elapsed_seconds"],
        "total_elapsed_seconds": result["total_elapsed_seconds"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    cli()
