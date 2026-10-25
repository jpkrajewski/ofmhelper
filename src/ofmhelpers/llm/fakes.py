"""What every provider fake (OFM_FAKE_EXTERNALS) shares: the answer a
model would give when a test queued none, and reading a wire field that
comes in either spelling."""

from typing import Any


def field(item: dict[str, Any], camel: str, snake: str) -> Any:
    """`item`'s field by either spelling: Google's JSON parser takes both,
    and the SDK writes some fields one way, some the other."""
    return item.get(camel, item.get(snake))


def example(schema: dict[str, Any], defs: dict[str, Any] | None = None) -> Any:
    """The plainest value `schema` (JSON Schema, as Pydantic writes it)
    allows."""
    if defs is None:
        defs = schema.get("$defs", {})
    if "$ref" in schema:
        return example(defs[str(schema["$ref"]).rsplit("/", 1)[-1]], defs)
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    for key in ("anyOf", "oneOf", "allOf"):
        options = [o for o in schema.get(key, []) if o.get("type") != "null"]
        if options:
            return example(options[0], defs)
    kind = schema.get("type")
    if kind == "object":
        props = schema.get("properties", {})
        return {name: example(sub, defs) for name, sub in props.items()}
    plain: dict[str, Any] = {
        "array": [],
        "string": "",
        "boolean": True,
        "integer": 0,
        "number": 0,
    }
    return plain.get(str(kind))
