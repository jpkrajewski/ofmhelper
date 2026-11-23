# Copyright (C) 2026 jpkrajewski

from typing import Any

lazy from pydantic import JsonValue


def field[T](item: dict[str, T], camel: str, snake: str) -> T | None:
    return item.get(camel, item.get(snake))


def example(schema: dict[str, Any], defs: dict[str, Any] | None = None) -> JsonValue:
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
