from __future__ import annotations

import json
import os
from pathlib import Path

import openpyxl

STORE_FILE = Path(
    os.getenv("OFM_BATTLE_TESTED_FILE", "/app/kieai_out/battle_tested.json")
)


def _extract_prompt_text(raw: str) -> str:
    """The 'prompt' column in these sheets is actually a JSON blob (the full
    kie.ai request payload), not plain text. Pull the human-readable prompt
    out of it. Handles two shapes seen in real exports:
      1. {"prompt": "...", ...}                       -- flat
      2. {"input": "{\"prompt\": \"...\", ...}", ...}  -- prompt nested as a
         JSON-encoded STRING inside "input"
    Falls back to the raw text if it isn't JSON at all."""
    raw = (raw or "").strip()
    if not raw:
        return ""

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw

    if isinstance(data, dict):
        if "prompt" in data:
            return str(data["prompt"]).strip()
        if "input" in data and isinstance(data["input"], str):
            try:
                inner = json.loads(data["input"])
                if isinstance(inner, dict) and "prompt" in inner:
                    return str(inner["prompt"]).strip()
            except (json.JSONDecodeError, TypeError):
                pass

    return raw


def parse_xlsx(path: Path) -> list[dict]:
    """Reads a sheet with title / prompt / link columns (case-insensitive,
    any order) and returns cleaned {title, prompt, link} dicts."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active

    rows = ws.iter_rows(values_only=True)
    header = [str(h).strip().lower() if h else "" for h in next(rows)]

    try:
        title_idx = header.index("title")
        prompt_idx = header.index("prompt")
        link_idx = header.index("link")
    except ValueError as exc:
        raise ValueError(
            "Sheet must have 'title', 'prompt', and 'link' columns"
        ) from exc

    results = []
    for row in rows:
        if row is None or all(c is None for c in row):
            continue
        title = str(row[title_idx]).strip() if row[title_idx] else ""
        raw_prompt = str(row[prompt_idx]) if row[prompt_idx] else ""
        link = str(row[link_idx]).strip() if row[link_idx] else ""
        if not title and not raw_prompt:
            continue
        results.append(
            {
                "title": title,
                "prompt": _extract_prompt_text(raw_prompt),
                "link": link,
            }
        )
    return results


def load_prompts() -> list[dict]:
    if not STORE_FILE.exists():
        return []
    try:
        return json.loads(STORE_FILE.read_text())
    except json.JSONDecodeError:
        return []


def save_prompts(prompts: list[dict]) -> None:
    STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STORE_FILE.write_text(json.dumps(prompts, indent=2))
