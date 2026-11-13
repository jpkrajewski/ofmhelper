"""Rebuild `integrations/ai_providers/runninghub/catalog.json` from RunningHub's docs.

RunningHub lists every documented endpoint in
`https://www.runninghub.ai/runninghub-api-doc-en/llms.txt`; each Standard
Model API page (`<page>.md`) embeds the OpenAPI spec of one
`POST /openapi/v2/<model path>` with a plain JSON body. This walks the image
and video pages and turns each body into the JSON schema `ModelSpec` reads
(the OpenAPI cleaning is the kie.ai script's):

    uv run python scripts/sync_runninghub_catalog.py

The model id is the path after `/openapi/v2/`. An entry already in the file
keeps its `label`, `family`, `fixed` and `default_prompt`; its schema is
regenerated. An entry the docs no longer list is kept (old jobs name it).

What the form cannot take is decided here, not per model:
- the docs' example values are not defaults: dropped on prompts, on
  media fields and on any field whose example is a URL (sample assets);
- media fields get `x-media` (camelCase `imageUrls`, `firstFrameUrl`, ...);
  `fileUrl`/`linkUrl` (a document / web page) stay plain text;
- a string enum of whole seconds (`duration`: "auto", "2".."30") becomes an
  integer range the form and /clone can fill, marked `x-wire: string`
  (the provider sends it back as "5");
- a model with a required field the form cannot fill is skipped and
  reported.

Audio, 3D and "Other" pages are skipped: the runner and /generate handle
image and video output only. Operator CLI: prints a report.
"""

import json
import re
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync_kie_catalog import (  # noqa: E402
    Skip,
    _check_form_fills,
    _get,
    _input_schema,
    _pin,
)

DOCS = "https://www.runninghub.ai/runninghub-api-doc-en"
OUT = ROOT / "src/ofmhelpers/integrations/ai_providers/runninghub/catalog.json"
API = "/openapi/v2/"
KINDS = {
    "Video Generation & Processing": "video",
    "Image Generation & Processing": "image",
}
_ENTRY = re.compile(
    r"^- Standard Model API > (?P<category>[^>\[]+?) > (?P<groups>[^\[]+?)\s*"
    r"\[(?P<title>[^\]]+)\]\((?P<url>https://www\.runninghub\.ai/[^)]+)\.md\)",
    re.MULTILINE,
)
_YAML = re.compile(r"```yaml\n(.*?)\n```", re.DOTALL)
_MEDIA_WORDS = (
    ("video", "video"),
    ("audio", "audio"),
    ("voice", "audio"),
    ("image", "image"),
    ("frame", "image"),
    ("mask", "image"),
)
_NOT_MEDIA = frozenset({"fileurl", "linkurl"})
_EXAMPLE_TEXT = frozenset({"prompt", "negativeprompt"})
_AUTO = "auto"


class Page(BaseModel, frozen=True):
    kind: str
    family: str
    title: str
    url: str


def _pages(skipped: list[str]) -> list[Page]:
    pages = []
    for m in _ENTRY.finditer(_get(f"{DOCS}/llms.txt")):
        kind = KINDS.get(m["category"].strip())
        if kind is None:
            skipped.append(f"{m['url']}: {m['category'].strip()} output")
            continue
        groups = [g.strip() for g in m["groups"].split(">")]
        family = groups[1] if len(groups) > 1 else ""
        pages.append(
            Page(
                kind=kind,
                family=family,
                title=" ".join(m["title"].split()),
                url=m["url"],
            )
        )
    return pages


def _media_kind(name: str, prop: dict[str, Any]) -> str | None:
    low = name.lower()
    if low in _NOT_MEDIA:
        return None
    items = prop.get("items") or {}
    is_array = prop.get("type") == "array"
    if is_array and items.get("type") not in ("string", None):
        return None
    if not is_array and prop.get("type") not in ("string", None):
        return None
    uri = prop.get("format") == "uri" or items.get("format") == "uri"
    if not (uri or low.endswith(("url", "urls"))):
        return None
    return next((kind for word, kind in _MEDIA_WORDS if word in low), None)


def _seconds_enum(prop: dict[str, Any]) -> dict[str, Any] | None:
    """A string enum of whole seconds (plus "auto") as an integer field."""
    enum = prop.get("enum")
    if prop.get("type") != "string" or not enum:
        return None
    numbers = [v for v in enum if v != _AUTO]
    if not numbers or not all(isinstance(v, str) and v.isdigit() for v in numbers):
        return None
    values = sorted(int(v) for v in numbers)
    out: dict[str, Any] = {"type": "integer", "x-wire": "string"}
    if values == list(range(values[0], values[-1] + 1)):
        out |= {"minimum": values[0], "maximum": values[-1]}
    else:
        out["enum"] = values
    default = prop.get("default")
    if isinstance(default, str) and default.isdigit():
        out["default"] = int(default)
    if prop.get("description"):
        out["description"] = prop["description"]
    return out


def _is_url(value: Any) -> bool:
    """A docs sample asset, never a default: `wire_body` would send it for a
    field left empty, and one (`draftCache`) was a signed link carrying the
    docs' own Tencent access key, which GitHub push protection refused."""
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _normalise(schema: dict[str, Any]) -> None:
    for name, prop in schema["properties"].items():
        kind = _media_kind(name, prop)
        if kind is not None:
            prop.pop("default", None)
            prop["x-media"] = kind
            if prop.get("type") == "array":
                prop["items"] = {**(prop.get("items") or {}), "type": "string"}
                prop["items"]["format"] = "uri"
            else:
                prop["type"] = "string"
                prop["format"] = "uri"
            continue
        if name.lower() in _EXAMPLE_TEXT or _is_url(prop.get("default")):
            prop.pop("default", None)
        if (seconds := _seconds_enum(prop)) is not None:
            schema["properties"][name] = seconds


def _label(page: Page) -> str:
    title = page.title.replace("/", " · ")
    if " " not in title:
        title = title.replace("-", " ")
    return title[:1].upper() + title[1:]


def _family(model_id: str, page: Page) -> str:
    if page.family:
        return page.family
    parts = model_id.split("/")
    name = parts[-2] if len(parts) > 1 else parts[0]
    return " ".join(w[:1].upper() + w[1:] for w in name.replace("-", " ").split())


def _entry(page: Page, current: dict[str, dict]) -> dict[str, Any]:
    block = _YAML.search(_get(f"{page.url}.md"))
    if not block:
        msg = "no OpenAPI spec on the page"
        raise Skip(msg)
    spec = yaml.safe_load(block.group(1))
    paths = [p for p in spec.get("paths") or {} if p.startswith(API)]
    if len(paths) != 1:
        msg = f"not one model path: {', '.join(spec.get('paths') or {})}"
        raise Skip(msg)
    post = (spec["paths"][paths[0]] or {}).get("post") or {}
    content = (post.get("requestBody") or {}).get("content") or {}
    body = (content.get("application/json") or {}).get("schema")
    if not body:
        msg = "no JSON body"
        raise Skip(msg)
    model_id = paths[0].removeprefix(API)
    components = (spec.get("components") or {}).get("schemas") or {}
    schema = _input_schema(body, components)
    _normalise(schema)
    keep = current.get(model_id) or {}
    fixed = _pin(schema, keep.get("fixed") or {})
    entry: dict[str, Any] = {
        "model_id": model_id,
        "label": keep.get("label") or _label(page),
        "family": keep.get("family") or _family(model_id, page),
        "kind": keep.get("kind") or page.kind,
        "input_schema": schema,
    }
    if fixed:
        entry["fixed"] = fixed
    if keep.get("default_prompt"):
        entry["default_prompt"] = keep["default_prompt"]
    if keep.get("max_upload_bytes"):
        entry["max_upload_bytes"] = keep["max_upload_bytes"]
    _check_form_fills(entry)
    return entry


def _built(pages: list[Page], current: dict[str, dict]) -> Iterator[tuple]:
    def one(page: Page) -> tuple:
        try:
            return page, _entry(page, current), None
        except Skip as reason:
            return page, None, str(reason)

    with ThreadPoolExecutor(8) as pool:
        yield from pool.map(one, pages)


def main() -> int:
    current = (
        {e["model_id"]: e for e in json.loads(OUT.read_text(encoding="utf-8"))}
        if OUT.is_file()
        else {}
    )
    skipped: list[str] = []
    pages = _pages(skipped)
    fresh: dict[str, dict] = {}
    for page, entry, reason in _built(pages, current):
        if reason:
            skipped.append(f"{page.url}: {reason}")
        else:
            fresh[entry["model_id"]] = entry
    added = sorted(
        (e for mid, e in fresh.items() if mid not in current),
        key=lambda e: (e["kind"], e["family"], e["label"]),
    )
    catalog = [fresh.get(mid, e) for mid, e in current.items()] + added
    OUT.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"{len(catalog)} models ({len(added)} new) from {len(pages)} pages -> {OUT}")
    for line in skipped:
        print(f"  skipped {line}")
    for model_id in current.keys() - fresh.keys():
        print(f"  kept {model_id}: no longer in the docs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
