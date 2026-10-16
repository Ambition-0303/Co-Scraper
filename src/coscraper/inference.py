"""OpenAI-compatible model inference and response normalization."""

from __future__ import annotations

import ast
import json
import re
import time
from typing import Any

DEFAULT_MODEL = "qwen-html"
THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)
FENCE_RE = re.compile(r"^```(?:python)?\s*|\s*```$", re.DOTALL | re.IGNORECASE)


class PipelineError(RuntimeError):
    pass


def normalize_base_url(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    if not value:
        raise PipelineError("missing model base URL or OPENAI_BASE_URL")
    if value.endswith("/chat/completions"):
        value = value[: -len("/chat/completions")]
    return value.rstrip("/") + "/"


def value_from(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            text = value_from(part, "text")
            if isinstance(text, str):
                parts.append(text)
        if parts:
            return "".join(parts)
    raise PipelineError("response message content is not text")


def reasoning_text(message: Any) -> str:
    for name in ("reasoning_content", "reasoning"):
        value = value_from(message, name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    model_extra = value_from(message, "model_extra")
    if isinstance(model_extra, dict):
        for name in ("reasoning_content", "reasoning"):
            value = model_extra.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def extract_reasoning_and_output(response: Any) -> tuple[str, str]:
    choices = value_from(response, "choices")
    if not isinstance(choices, list) or not choices:
        raise PipelineError("response has no choices")
    message = value_from(choices[0], "message")
    if message is None:
        raise PipelineError("response choice has no message")
    output = content_text(value_from(message, "content")).strip()
    reasoning = reasoning_text(message)
    think_parts = [part.strip() for part in THINK_RE.findall(output) if part.strip()]
    if think_parts:
        if not reasoning:
            reasoning = "\n\n".join(think_parts)
        output = THINK_RE.sub("", output).strip()
    if not output:
        raise PipelineError("response final output is empty")
    return reasoning, output


def inference(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str,
    base_url: str,
    api_key: str = "",
    max_tokens: int = 8192,
    timeout: float = 600.0,
    max_attempts: int = 3,
    json_mode: bool = False,
) -> tuple[str, str]:
    """Call an OpenAI-compatible chat endpoint and return (reasoning, output)."""
    if max_attempts <= 0:
        raise ValueError("max_attempts must be positive")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise PipelineError(
            "OpenAI SDK is unavailable; install the package with the openai extra or enable an OpenAI-compatible environment."
        ) from exc

    client = OpenAI(
        api_key=api_key or "not-required",
        base_url=normalize_base_url(base_url),
        timeout=timeout,
        max_retries=0,
    )
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    last_error = ""
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.chat.completions.create(**payload)
            return extract_reasoning_and_output(response)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < max_attempts:
                time.sleep(min(2 ** (attempt - 1), 8))
    raise PipelineError(f"model inference failed after {max_attempts} attempts: {last_error}")


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        parsed_lines = stripped.splitlines()
        parsed_lines = parsed_lines[1:]
        if parsed_lines and parsed_lines[-1].strip() == "```":
            parsed_lines = parsed_lines[:-1]
        stripped = "\n".join(parsed_lines).strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for position, character in enumerate(stripped):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(stripped[position:])
                break
            except json.JSONDecodeError:
                continue
        else:
            raise PipelineError("model output contains no valid JSON object")
    if not isinstance(value, dict):
        raise PipelineError("model output must contain a JSON object")
    return value


def normalize_useful_item_ids(output: str, available_ids: set[str]) -> list[str]:
    value = parse_json_object(output)
    raw_ids = value.get("useful_item_ids")
    if not isinstance(raw_ids, list):
        raise PipelineError("useful_item_ids must be a list")
    normalized = []
    seen = set()
    for raw_id in raw_ids:
        if isinstance(raw_id, bool):
            raise PipelineError("boolean useful_item_id is invalid")
        if isinstance(raw_id, int):
            item_id = str(raw_id)
        elif isinstance(raw_id, str) and raw_id.strip().isdigit():
            item_id = str(int(raw_id.strip()))
        else:
            raise PipelineError(f"non-numeric useful_item_id: {raw_id!r}")
        if item_id not in seen:
            seen.add(item_id)
            normalized.append(item_id)
    if not normalized:
        raise PipelineError("useful_item_ids is empty")
    unavailable = [item_id for item_id in normalized if item_id not in available_ids]
    if unavailable:
        raise PipelineError(f"useful_item_ids not present in mapped HTML: {unavailable}")
    return normalized


def normalize_and_validate_code(output: str) -> str:
    code = output.strip()
    if code.startswith("```"):
        code = FENCE_RE.sub("", code).strip()
    if "```" in code:
        raise PipelineError("model output contains markdown fences")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise PipelineError(f"generated code is invalid Python: {exc}") from exc
    parser_classes = [
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "WebPageParser"
    ]
    if not parser_classes or not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "parse"
        for node in parser_classes[0].body
    ):
        raise PipelineError("generated code lacks WebPageParser.parse")
    if not any(isinstance(node, ast.FunctionDef) and node.name == "main" for node in tree.body):
        raise PipelineError("generated code lacks main()")
    has_main_guard = any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        for node in tree.body
    )
    if not has_main_guard:
        raise PipelineError("generated code lacks __main__ guard")
    return code
