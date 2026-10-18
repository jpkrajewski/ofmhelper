"""Rebuild `aigenproviders/higgsfield/catalog.json` from Higgsfield's docs.

Higgsfield documents every model as one Markdown page per workflow
(`docs/models/<family>/<workflow>.md`) carrying its endpoint id and the
complete JSON schema of its input. This walks both category pages, then each
family, then each workflow, and writes what it finds -- the one place the
catalog comes from, so adding the models Higgsfield ships later is a re-run:

    uv run python scripts/sync_higgsfield_catalog.py

Operator CLI: prints a report rather than logging.
"""

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

DOCS = "https://docs.higgsfield.ai"
CATEGORIES = {"video": "video-generation", "image": "image-generation"}
OUT = (
    Path(__file__).resolve().parents[1]
    / "src/ofmhelpers/aigenproviders/higgsfield/catalog.json"
)
TIMEOUT_S = 30

_FAMILY_CARD = re.compile(
    r'href="/docs/models/([a-z0-9-]+)">.*?featured-model-title">([^<]+)<', re.DOTALL
)
_ENDPOINT_ID = re.compile(r"\*\*Endpoint ID:\*\* `([^`]+)`")
_TITLE = re.compile(r"^# (.+?)(?: API)?$", re.MULTILINE)
_SCHEMA = re.compile(r"Complete JSON schema.*?```json[^\n]*\n(.*?)\n\s*```", re.DOTALL)


def _get(path: str) -> str:
    response = requests.get(f"{DOCS}{path}.md", timeout=TIMEOUT_S)
    response.raise_for_status()
    return response.text


def _families() -> list[tuple[str, str, str]]:
    """(slug, family title, kind) for every model family."""
    return [
        (slug, title.strip(), kind)
        for kind, page in CATEGORIES.items()
        for slug, title in _FAMILY_CARD.findall(_get(f"/docs/models/{page}"))
    ]


def _workflow(slug: str, family: str, kind: str, workflow: str) -> dict | None:
    page = _get(f"/docs/models/{slug}/{workflow}")
    endpoint = _ENDPOINT_ID.search(page)
    schema = _SCHEMA.search(page)
    if not endpoint or not schema:
        return None  # a training workflow (Soul ID) or an empty page
    title = _TITLE.search(page)
    workflow_label = title.group(1).split("—", 1)[-1].strip() if title else workflow
    return {
        "model_id": endpoint.group(1),
        "label": f"{family} · {workflow_label}",
        "family": family,
        "kind": kind,
        "input_schema": json.loads(schema.group(1)),
    }


def _workflows(slug: str, family: str, kind: str) -> list[dict]:
    names = sorted(
        set(
            re.findall(
                rf"/docs/models/{slug}/([a-z0-9-]+)", _get(f"/docs/models/{slug}")
            )
        )
    )
    found = [_workflow(slug, family, kind, name) for name in names]
    return [entry for entry in found if entry]


def main() -> int:
    families = _families()
    with ThreadPoolExecutor(8) as pool:
        groups = pool.map(lambda f: _workflows(*f), families)
    entries = {e["model_id"]: e for group in groups for e in group}
    catalog = sorted(
        entries.values(), key=lambda e: (e["kind"], e["family"], e["label"])
    )
    OUT.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"{len(catalog)} models from {len(families)} families -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
