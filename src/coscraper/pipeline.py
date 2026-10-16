"""End-to-end Co-Scraper pipeline."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Sequence

from .dom import ITEM_ID_ATTR, _parse_document, extract_main_html, simplify_html
from .inference import (
    DEFAULT_MODEL,
    PipelineError,
    inference,
    normalize_and_validate_code,
    normalize_useful_item_ids,
)
from .prompts import (
    CODE_INSTRUCTION_TEMPLATE,
    CODE_PROMPT,
    USEFUL_IDS_INSTRUCTION_TEMPLATE,
    USEFUL_IDS_SYSTEM_PROMPT,
)


def available_item_ids(map_html: str) -> set[str]:
    """Return every item ID present in a mapped HTML document."""
    root = _parse_document(map_html)
    return {
        str(item_id)
        for item_id in root.xpath(f"//*[@{ITEM_ID_ATTR}]/@{ITEM_ID_ATTR}")
    }


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_outputs(result: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    """Persist the complete result, generated code, and extracted seed HTML."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {
        "result": directory / "result.json",
        "generated_code": directory / "generated_code.py",
    }
    for index, page in enumerate(result["pages"], start=1):
        path = directory / f"seed_{index:03d}_extracted.html"
        path.write_text(page["extracted_html"], encoding="utf-8")
        paths[f"seed_{index:03d}_extracted_html"] = str(path)
    result["outputs"] = {name: str(path) for name, path in paths.items()}
    paths["result"].write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    paths["generated_code"].write_text(result["generated_code"], encoding="utf-8")
    return result["outputs"]


def run(
    query: str,
    schema: dict[str, Any],
    htmls: Sequence[str],
    *,
    model: str = DEFAULT_MODEL,
    base_url: str | None = None,
    api_key: str | None = None,
    id_max_tokens: int = 8192,
    code_max_tokens: int = 32768,
    timeout: float = 600.0,
    max_attempts: int = 3,
    json_mode: bool = False,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run query-aware pruning and synthesize a reusable scraper.

    Args:
        query: Natural-language extraction request.
        schema: JSON Schema describing the desired output object.
        htmls: Exactly three structurally similar seed HTML documents.
        model: Model ID exposed by the OpenAI-compatible endpoint.
        base_url: API base URL. Defaults to ``OPENAI_BASE_URL``.
        api_key: API key. Defaults to ``OPENAI_API_KEY``.
        output_dir: Optional directory for result artifacts.

    Returns:
        A dictionary containing per-page pruning traces, model reasoning,
        generated Python code, and timing information.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if not isinstance(schema, dict):
        raise ValueError("schema must be a JSON object")
    documents = list(htmls)
    if len(documents) != 3:
        raise ValueError("exactly three seed HTML documents are required")
    if any(not isinstance(document, str) or not document.strip() for document in documents):
        raise ValueError("each seed HTML must be a non-empty string")

    started = time.perf_counter()
    schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    pages = []
    for index, raw_html in enumerate(documents, start=1):
        stage_started = time.perf_counter()
        simplified_html, map_html = simplify_html(raw_html, cutoff_length=500)
        candidate_ids = available_item_ids(map_html)
        if not candidate_ids:
            raise PipelineError(f"seed HTML {index}: no _item_id values were generated")
        instruction = USEFUL_IDS_INSTRUCTION_TEMPLATE.format(
            html=simplified_html,
            query=query,
            schema=schema_text,
        )
        reasoning, output = inference(
            USEFUL_IDS_SYSTEM_PROMPT,
            instruction,
            model=model,
            base_url=base_url or os.environ.get("OPENAI_BASE_URL", ""),
            api_key=api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", ""),
            max_tokens=id_max_tokens,
            timeout=timeout,
            max_attempts=max_attempts,
            json_mode=json_mode,
        )
        useful_ids = normalize_useful_item_ids(output, candidate_ids)
        extracted_html = extract_main_html(map_html, {"useful_item_ids": useful_ids})
        if not extracted_html.strip():
            raise PipelineError(f"seed HTML {index}: extracted HTML is empty")
        pages.append({
            "index": index,
            "raw_sha256": sha256_text(raw_html),
            "simplified_html": simplified_html,
            "mapped_html": map_html,
            "useful_item_ids": useful_ids,
            "id_reasoning": reasoning,
            "extracted_html": extracted_html,
            "elapsed_seconds": round(time.perf_counter() - stage_started, 3),
        })

    code_started = time.perf_counter()
    code_instruction = CODE_INSTRUCTION_TEMPLATE.format(
        html1=pages[0]["extracted_html"],
        html2=pages[1]["extracted_html"],
        html3=pages[2]["extracted_html"],
        query=query,
        schema=schema_text,
    )
    code_reasoning, code_output = inference(
        CODE_PROMPT,
        code_instruction,
        model=model,
        base_url=base_url or os.environ.get("OPENAI_BASE_URL", ""),
        api_key=api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", ""),
        max_tokens=code_max_tokens,
        timeout=timeout,
        max_attempts=max_attempts,
    )
    generated_code = normalize_and_validate_code(code_output)
    result = {
        "query": query,
        "schema": schema,
        "model": model,
        "pages": pages,
        "code_reasoning": code_reasoning,
        "generated_code": generated_code,
        "id_elapsed_seconds": round(sum(page["elapsed_seconds"] for page in pages), 3),
        "code_elapsed_seconds": round(time.perf_counter() - code_started, 3),
        "total_elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    if output_dir is not None:
        write_outputs(result, output_dir)
    return result
