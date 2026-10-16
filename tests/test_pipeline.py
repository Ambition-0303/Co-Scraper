from __future__ import annotations

import json
from pathlib import Path

import pytest

from coscraper import extract_main_html, run, simplify_html
from coscraper.pipeline import available_item_ids


def test_simplify_and_reconstruct() -> None:
    source = '''<html><body><nav>x</nav><h1>Camera A</h1><div class="price">$499</div></body></html>'''
    simplified, mapped = simplify_html(source)
    item_ids = available_item_ids(mapped)
    assert item_ids
    assert "_item_id" in simplified
    extracted = extract_main_html(mapped, {"useful_item_ids": sorted(item_ids, key=int)})
    assert "Camera A" in extracted
    assert "$499" in extracted


def test_run_requires_three_seed_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_inference(*args, **kwargs):
        raise AssertionError("inference should not be called")

    monkeypatch.setattr("coscraper.pipeline.inference", fake_inference)
    with pytest.raises(ValueError, match="exactly three"):
        run("Extract model and price", {}, ["<html></html>"])


def test_run_end_to_end_with_fake_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_inference(system_prompt, user_prompt, **kwargs):
        if system_prompt.__contains__("HTML data cleaning"):
            return "", json.dumps({"useful_item_ids": ["1", "2", "3"]})
        return "", '''import json
from lxml import html
class WebPageParser:
    def parse(self, html_text):
        return {"model": "", "price": ""}
def main():
    pass
if __name__ == "__main__":
    main()
'''

    monkeypatch.setattr("coscraper.pipeline.inference", fake_inference)
    pages = [
        f"<html><body><h1>Camera {letter}</h1><div class='price'>${value}</div><nav>x</nav></body></html>"
        for letter, value in zip("ABC", (499, 599, 699), strict=True)
    ]
    result = run(
        "Extract camera model and price.",
        {"type": "object", "properties": {"model": {"type": "string"}, "price": {"type": "string"}}},
        pages,
        output_dir=tmp_path,
    )
    assert len(result["pages"]) == 3
    assert all(page["useful_item_ids"] for page in result["pages"])
    assert "class WebPageParser" in result["generated_code"]
    assert (tmp_path / "result.json").is_file()
    assert (tmp_path / "generated_code.py").is_file()
    assert json.loads((tmp_path / "result.json").read_text())["outputs"]
