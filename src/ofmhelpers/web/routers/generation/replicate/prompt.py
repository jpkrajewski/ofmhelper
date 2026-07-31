"""Turning the review page's edited JSON back into what Seedance is sent."""

import json
from typing import Any


def _drop_nulls(value: Any) -> Any:
    """Strip every null out of the prompt, at any depth.

    The analysis prompt asks for nulls as a deliberate signal to *itself* --
    "line: null if no dialogue", "pose: null if off camera" -- so a fully
    filled-in answer still carries a null on most scene_events entries. Seedance
    reads the prompt as instructions, not as a schema, and an absent key says
    exactly what a null one does while costing nothing to read.

    Only nulls go. An empty string is a real (if unhelpful) answer and stays."""
    if isinstance(value, dict):
        return {k: _drop_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_nulls(v) for v in value if v is not None]
    return value


def _minify_prompt_json(script: str) -> str:
    """Seedance is handed this string verbatim, and a <textarea> submits its
    value with every newline normalized to CRLF (HTML spec) -- so the
    pretty-printed JSON the review page shows arrived as a blob full of \\r\\n
    and went straight to the provider that way. Re-serialize it compactly
    instead: one line, no indentation, no carriage returns, no nulls.

    Text that isn't JSON falls through with newlines normalized rather than
    being rejected: a VA fixing an unvalidated raw answer by hand still has to
    be able to submit it."""
    try:
        data = json.loads(script)
    except json.JSONDecodeError:
        return script.replace("\r\n", "\n").replace("\r", "\n")
    return json.dumps(_drop_nulls(data), separators=(",", ":"), ensure_ascii=False)
