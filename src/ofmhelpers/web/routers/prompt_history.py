from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/prompt-history", tags=["prompt-history"])

TASK_LOG = Path(os.getenv("OFM_KIEAI_TASK_LOG", "/app/kieai_out/tasks.jsonl"))
MAX_RETURNED = 30


@router.get("")
def recent_prompts():
    """Reads the existing tasks.jsonl (already written by KieAIClient on every
    generation) and returns a deduplicated, most-recent-first list of
    {model, prompt, createdAt} for display as clickable history chips.
    No separate storage -- this is just a view over data that already exists."""
    if not TASK_LOG.exists():
        return []

    seen: set[tuple[str, str]] = set()
    items: list[dict] = []

    lines = TASK_LOG.read_text().splitlines()
    for line in reversed(lines):  # newest first, since it's append-only
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        prompt = (rec.get("prompt") or "").strip()
        model = rec.get("model", "")
        if not prompt:
            continue

        key = (model, prompt)
        if key in seen:
            continue
        seen.add(key)

        items.append(
            {
                "model": model,
                "prompt": prompt,
                "createdAt": rec.get("createdAt"),
            }
        )
        if len(items) >= MAX_RETURNED:
            break

    return items
