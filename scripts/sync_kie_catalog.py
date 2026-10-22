"""Rebuild `aigenproviders/kaiai/catalog.json` from kie.ai's docs.

kie.ai lists every documented endpoint in `https://docs.kie.ai/llms.txt`;
each model page (`<page>.md`) embeds the OpenAPI spec of its request, and
since 2026 every generation model -- Market, Veo 3.1, Runway, 4o Image,
Flux Kontext, Suno -- is a `POST /api/v1/jobs/createTask` with `model` plus
an `input` object. This walks the image and video pages, turns each `input`
into the JSON schema `ModelSpec` reads, and merges it into the catalog:

    uv run python scripts/sync_kie_catalog.py

Merge rules (the catalog is shared with jobs and /clone, so ids never move):
- `CURATED` entries are kept verbatim: their schemas were hand-tuned to the
  bodies the old per-model wrappers sent.
- Every other entry already in the file keeps its `label`, `family`,
  `fixed` and `default_prompt`; its `input_schema` is regenerated.
- An entry the docs no longer list is kept (old jobs name it) and reported.

What the form cannot take is decided here, not per model:
- media fields get `x-media` (picked by name: video/clip, audio/voice,
  image/frame/mask/input/file) and `format: uri`;
- `allOf` is dropped (the Kling 3.0 Omni pages carry `allOf: [{type:
  string}, ...]` on an object -- an export artefact no input could pass);
- `pattern` is dropped (local paths stand in for URLs in `ModelSpec.check`,
  and `^.*\\S.*$` on a prompt rejects multi-line text);
- an input named like a field of the /ai/run form itself (`model`) is pinned
  in `fixed` to its default;
- `nsfw_checker` is pinned to false, as on the curated entries;
- a model with a required field the form cannot fill (arrays of objects) or
  one answering with `resultObject` only (masks, detection data; the client
  reads `resultUrls`) is skipped and reported.

Audio (Music) and chat models are skipped: the runner and /generate handle
image and video output only.

Operator CLI: prints a report rather than logging.
"""

import json
import re
import sys
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests
import yaml
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ofmhelpers.aigenproviders.spec import ModelSpec  # noqa: E402

DOCS = "https://docs.kie.ai"
OUT = ROOT / "src/ofmhelpers/aigenproviders/kaiai/catalog.json"
TIMEOUT_S = 30
CREATE_TASK = "/api/v1/jobs/createTask"
KINDS = {"Image": "image", "Video": "video"}

# Hand-made before this script existed; never regenerated.
CURATED = frozenset(
    {
        "bytedance/seedance-2",
        "bytedance/seedance-2-fast",
        "bytedance/seedance-2-mini",
        "bytedance/seedance-2-5",
        "kling-3.0/video",
        "wan/3-0-video",
        "minimax-h3/text-to-video",
        "minimax-h3/image-to-video",
        "minimax-h3/reference-to-video",
        "nano-banana-pro",
        "seedream/4.5-text-to-image",
        "seedream/4.5-edit",
        "seedream/5-lite-text-to-image",
        "seedream/5-lite-image-to-image",
        "seedream/5-pro-text-to-image",
        "seedream/5-pro-image-to-image",
        "gpt-image-2-5-flare-image-to-image",
    }
)
# Field names /ai/run uses itself (`AiRunForm`): a model input of that name
# would collide with them, so it is sent fixed instead.
RESERVED = frozenset({"provider", "model", "api_key"})
ALWAYS_FIXED: dict[str, Any] = {"nsfw_checker": False}

_ENTRY = re.compile(
    r"^- (?P<cat>\w+)\s+Models > (?P<group>[^\[]+?) \[(?P<title>[^\]]+)\]"
    r"\((?P<url>https://docs\.kie\.ai/(?!cn/)[^)]+)\.md\)",
    re.MULTILINE,
)
_YAML = re.compile(r"```yaml\n(.*?)\n```", re.DOTALL)
_KEEP = frozenset(
    {
        "type",
        "title",
        "description",
        "enum",
        "const",
        "default",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "uniqueItems",
        "format",
        "items",
        "properties",
        "required",
        "anyOf",
    }
)
_MEDIA_WORDS = (
    ("video", "video"),
    ("clip", "video"),
    ("audio", "audio"),
    ("voice", "audio"),
    ("image", "image"),
    ("frame", "image"),
    ("mask", "image"),
    ("input", "image"),
    ("file", "image"),
)
_BARE_MEDIA = frozenset(
    {"image", "input_image", "reference_image", "reference_video", "first_frame"}
)
_NOT_MEDIA = frozenset({"reference_link_urls", "reference_file_urls"})
_SCALAR_OF = {str: "string", bool: "boolean", int: "integer", float: "number"}
_MAX_DESCRIPTION = 240


@dataclass(frozen=True)
class Page:
    kind: str
    family: str
    title: str
    url: str


class Skip(Exception):  # noqa: N818 -- a reason, not a failure
    pass


@lru_cache(maxsize=1)
def _session() -> requests.Session:
    """docs.kie.ai times out now and then under 8 parallel reads."""
    retry = Retry(total=4, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504))
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=8))
    return session


def _get(url: str) -> str:
    response = _session().get(url, timeout=TIMEOUT_S)
    response.raise_for_status()
    return response.text


def _pages(skipped: list[str]) -> list[Page]:
    """Every image/video API page in llms.txt (English, no callback pages);
    the other categories are counted into `skipped`."""
    text = _get(f"{DOCS}/llms.txt").split("## API Docs", 1)[-1]
    pages = []
    other: Counter[str] = Counter()
    for m in _ENTRY.finditer(text):
        title = " ".join(m["title"].split())
        if "callback" in title.lower():
            continue
        if m["cat"] not in KINDS:
            other[m["cat"]] += 1
            continue
        family = m["group"].split(">")[0].strip().removesuffix(" API").strip()
        pages.append(Page(KINDS[m["cat"]], family, title, m["url"]))
    skipped.extend(
        f"{count} {cat} pages: output is not image/video"
        for cat, count in other.items()
    )
    return pages


def _description(text: str) -> str:
    text = " ".join(re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text).split())
    if len(text) <= _MAX_DESCRIPTION:
        return text
    cut = text[:_MAX_DESCRIPTION]
    end = cut.rfind(". ")
    return cut[: end + 1] if end > 0 else cut.rstrip() + "…"


def _resolve(node: Any, components: dict[str, Any]) -> Any:
    if isinstance(node, dict) and "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1].replace("%20", " ")
        return _resolve(components[name], components)
    return node


def _clean(node: Any, components: dict[str, Any]) -> Any:
    """One OpenAPI 3.0 schema node as JSON schema 2020-12, trimmed to what
    validation and the form use."""
    node = _resolve(node, components)
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key not in _KEEP:
            continue
        if key == "format" and value != "uri":
            continue
        if key == "description":
            if text := _description(str(value)):
                out[key] = text
        elif key == "items":
            out[key] = _clean(value, components)
        elif key == "properties":
            out[key] = _properties(value, node.get("required") or [], components)
        elif key == "anyOf":
            out[key] = [_clean(v, components) for v in value]
        else:
            out[key] = value
    _openapi_30(node, out)
    if "type" not in out and out.get("enum"):
        out["type"] = _SCALAR_OF.get(type(out["enum"][0]), "string")
    if "oneOf" in node:  # exactly-one would fail on overlapping variants
        out["anyOf"] = [_clean(v, components) for v in node["oneOf"]]
    return out


def _openapi_30(node: dict[str, Any], out: dict[str, Any]) -> None:
    """OpenAPI 3.0's `nullable` and boolean `exclusiveMinimum/Maximum`."""
    if node.get("nullable") and isinstance(out.get("type"), str):
        out["type"] = [out["type"], "null"]
    for bound, limit in (
        ("exclusiveMinimum", "minimum"),
        ("exclusiveMaximum", "maximum"),
    ):
        if isinstance(out.get(bound), bool) and out.pop(bound) and limit in out:
            out[bound] = out.pop(limit)


def _properties(
    props: dict[str, Any], required: list[str], components: dict[str, Any]
) -> dict[str, Any]:
    """Deprecated properties are left out unless required."""
    return {
        name: _clean(value, components)
        for name, value in props.items()
        if not (_resolve(value, components).get("deprecated") and name not in required)
    }


def _loosen(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """One property defined by several input variants: what any of them
    accepts (each variant still validates exactly through `anyOf`)."""
    out = dict(a)
    for key in ("minimum", "minItems", "minLength", "maximum", "maxItems", "maxLength"):
        if key not in a or key not in b:
            out.pop(key, None)
        elif a[key] != b[key]:
            out[key] = (min if key.startswith("min") else max)(a[key], b[key])
    if "enum" in a and "enum" in b:
        out["enum"] = a["enum"] + [v for v in b["enum"] if v not in a["enum"]]
    elif "enum" in a:
        out.pop("enum")
    if a.get("default") != b.get("default"):
        out.pop("default", None)
    return out


def _input_schema(raw: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    """The `input` object; variants (`oneOf`) become one form: every field of
    any variant, required only where all require it."""
    schema = _clean(raw, components)
    variants = schema.pop("anyOf", None)
    if variants:
        props: dict[str, Any] = {}
        for variant in variants:
            for name, value in (variant.get("properties") or {}).items():
                props[name] = _loosen(props[name], value) if name in props else value
        required = set.intersection(*(set(v.get("required") or []) for v in variants))
        schema["properties"] = {**props, **(schema.get("properties") or {})}
        schema["required"] = sorted(required | set(schema.get("required") or []))
        schema["anyOf"] = variants
    schema["type"] = "object"
    schema.setdefault("properties", {})
    if not schema.get("required"):
        schema.pop("required", None)
    schema["additionalProperties"] = False
    return schema


def _media_kind(name: str, prop: dict[str, Any]) -> str | None:
    if name in _NOT_MEDIA:
        return None
    items = prop.get("items") or {}
    is_array = prop.get("type") == "array"
    if is_array and items.get("type") not in ("string", None):
        return None
    if not is_array and prop.get("type") not in ("string", None):
        return None
    uri = prop.get("format") == "uri" or items.get("format") == "uri"
    if not (uri or name.endswith(("_url", "_urls")) or name in _BARE_MEDIA):
        return None
    return next((kind for word, kind in _MEDIA_WORDS if word in name), None)


def _mark_media(schema: dict[str, Any]) -> None:
    """`x-media` and `format: uri` on every top-level media field."""
    for name, prop in schema["properties"].items():
        kind = _media_kind(name, prop)
        if kind is None:
            continue
        prop["x-media"] = kind
        if prop.get("type") == "array":
            prop["items"] = {**(prop.get("items") or {}), "type": "string"}
            prop["items"]["format"] = "uri"
        else:
            prop["type"] = "string"
            prop["format"] = "uri"


def _pin(schema: dict[str, Any], fixed: dict[str, Any]) -> dict[str, Any]:
    """Fields sent fixed, out of the schema: reserved names at their default,
    `ALWAYS_FIXED`, and whatever the existing entry already fixes."""
    props = schema["properties"]
    pinned = dict(fixed)
    for name in list(props):
        if name in RESERVED:
            value = props[name].get("default", (props[name].get("enum") or [None])[0])
            if value is None:
                msg = f"input field {name!r} collides with the /ai/run form"
                raise Skip(msg)
            pinned.setdefault(name, value)
        elif name in ALWAYS_FIXED:
            pinned.setdefault(name, ALWAYS_FIXED[name])
    for name in pinned:
        props.pop(name, None)
        if "required" in schema:
            schema["required"] = [r for r in schema["required"] if r != name]
    for variant in schema.get("anyOf", []):
        for name in pinned:
            (variant.get("properties") or {}).pop(name, None)
            if "required" in variant:
                variant["required"] = [r for r in variant["required"] if r != name]
    if not schema.get("required"):
        schema.pop("required", None)
    return pinned


def _label(page: Page) -> str:
    title = page.title.replace(" - ", " · ")
    if page.family.lower() in title.lower():
        return title
    return f"{page.family} · {title}"


def _entry(page: Page, current: dict[str, dict]) -> dict[str, Any]:
    text = _get(f"{page.url}.md")
    block = _YAML.search(text)
    if not block:
        msg = "no OpenAPI spec on the page"
        raise Skip(msg)
    spec = yaml.safe_load(block.group(1))
    paths = spec.get("paths") or {}
    if CREATE_TASK not in paths:
        msg = f"own endpoint {', '.join(paths)}"
        raise Skip(msg)
    if '{"resultObject"' in text and '"resultUrls"' not in text:
        msg = "answers with resultObject only (no resultUrls)"
        raise Skip(msg)
    body = paths[CREATE_TASK]["post"]["requestBody"]["content"]["application/json"]
    props = body["schema"]["properties"]
    model = props.get("model") or {}
    model_id = model.get("default") or (model.get("enum") or [None])[0]
    if not model_id:
        msg = "no model id in the spec"
        raise Skip(msg)
    components = (spec.get("components") or {}).get("schemas") or {}
    schema = _input_schema(props.get("input") or {}, components)
    _mark_media(schema)
    keep = current.get(model_id) or {}
    fixed = _pin(schema, keep.get("fixed") or {})
    entry: dict[str, Any] = {
        "model_id": model_id,
        "label": keep.get("label") or _label(page),
        "family": keep.get("family") or page.family,
        "kind": keep.get("kind") or page.kind,
        "input_schema": schema,
    }
    if fixed:
        entry["fixed"] = fixed
    if keep.get("default_prompt"):
        entry["default_prompt"] = keep["default_prompt"]
    _check_form_fills(entry)
    return entry


def _check_form_fills(entry: dict[str, Any]) -> None:
    """Every required field must be one the form renders."""
    spec = ModelSpec.model_validate({**entry, "provider": "kie"})
    fillable = {f.name for f in spec.form_fields} | {f.name for f in spec.media_fields}
    missing = sorted(spec.required - fillable)
    if missing:
        msg = f"required field(s) the form cannot fill: {', '.join(missing)}"
        raise Skip(msg)


def _built(pages: list[Page], current: dict[str, dict]) -> Iterator[tuple]:
    """(page, entry or None, skip reason or None) per page."""

    def one(page: Page) -> tuple:
        try:
            return page, _entry(page, current), None
        except Skip as reason:
            return page, None, str(reason)

    with ThreadPoolExecutor(8) as pool:
        yield from pool.map(one, pages)


def main() -> int:
    current = {e["model_id"]: e for e in json.loads(OUT.read_text(encoding="utf-8"))}
    skipped: list[str] = []
    pages = _pages(skipped)
    fresh: dict[str, dict] = {}
    for page, entry, reason in _built(pages, current):
        if reason:
            skipped.append(f"{page.url}: {reason}")
        elif entry["model_id"] not in CURATED:
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
    for model_id in current.keys() - CURATED - fresh.keys():
        print(f"  kept {model_id}: no longer in the docs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
