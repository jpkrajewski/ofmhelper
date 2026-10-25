"""An OpenAI-style `/chat/completions` vendor faked at the wire
(OFM_FAKE_EXTERNALS, tests), behind an `httpx.MockTransport`: one per
vendor (its `fake.py`), each with its host, key, models and image cap.

The answer is the next queued one (`answer_next`, `fail_next`,
`filter_next`), else the plainest value the request's strict JSON Schema
allows (`fakes.example`). Like the vendors: any key but the fake's is a 401,
a model not offered a 404, more images than the cap a 400, and a schema not
in strict form (an open object, an optional property) a 400.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import httpx

from ofmhelpers.llm.fakes import example


def _error(
    code: int,
    message: str,
    retry_after_s: float | None = None,
    error_code: str | None = None,
    generated: str | None = None,
) -> httpx.Response:
    headers = {"retry-after": f"{retry_after_s:g}"} if retry_after_s is not None else {}
    error: dict[str, Any] = {"message": message, "type": "invalid_request_error"}
    if error_code:
        error["code"] = error_code
    if generated is not None:
        error["failed_generation"] = generated
    return httpx.Response(code, json={"error": error}, headers=headers)


def _not_strict(node: Any) -> str | None:
    """Why `node` is not a strict-mode schema, if it is not."""
    if isinstance(node, list):
        return next((r for r in map(_not_strict, node) if r), None)
    if not isinstance(node, dict):
        return None
    if "$ref" in node or "$defs" in node:
        return "references are not inlined"
    if node.get("type") == "object":
        props = node.get("properties", {})
        if node.get("additionalProperties") is not False:
            return "additionalProperties must be false"
        if set(node.get("required", [])) != set(props):
            return "every property must be required"
        return next((r for r in map(_not_strict, props.values()) if r), None)
    return next((r for r in map(_not_strict, node.values()) if r), None)


class FakeChatCompletions:
    def __init__(self, host: str, key: str, models: frozenset[str], max_images: int):
        self.host = host
        self.key = key
        self.models = models
        self.max_images = max_images
        # Every chat/completions body, oldest first.
        self.requests: list[dict[str, Any]] = []
        self._replies: list[httpx.Response | str] = []
        self._lock = threading.Lock()

    # --- test hooks ---------------------------------------------------------

    def answer_next(self, answer: dict[str, Any] | str) -> None:
        with self._lock:
            self._replies.append(
                answer if isinstance(answer, str) else json.dumps(answer)
            )

    def fail_next(
        self, code: int, message: str, retry_after_s: float | None = None
    ) -> None:
        with self._lock:
            self._replies.append(_error(code, message, retry_after_s))

    def misanswer_next(self, generated: str) -> None:
        """The next answer fails the vendor's own schema check (Groq's 400
        `json_validate_failed`, the text it generated attached)."""
        with self._lock:
            self._replies.append(
                _error(
                    400,
                    "Failed to generate JSON. Please adjust your prompt.",
                    error_code="json_validate_failed",
                    generated=generated,
                )
            )

    def filter_next(self) -> None:
        """The next answer is stopped by the vendor's content filter."""
        with self._lock:
            self._replies.append(self._completion(None, "content_filter"))

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._answer)

    # --- the wire -----------------------------------------------------------

    def _answer(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != self.host:
            return _error(404, f"Unknown host {request.url.host}")
        if request.headers.get("Authorization") != f"Bearer {self.key}":
            return _error(401, "Invalid API Key")
        if not (
            request.method == "POST" and request.url.path.endswith("/chat/completions")
        ):
            return _error(404, f"Unknown request {request.method} {request.url.path}")
        body = json.loads(request.content or b"{}")
        with self._lock:
            self.requests.append(body)
            queued = self._replies.pop(0) if self._replies else None
        if isinstance(queued, httpx.Response):
            return queued
        refused = self._refused(body)
        if refused is not None:
            return refused
        if queued is None:
            schema = body["response_format"]["json_schema"]["schema"]
            queued = json.dumps(example(schema))
        return self._completion(queued, "stop")

    def _refused(self, body: dict[str, Any]) -> httpx.Response | None:
        model = body.get("model")
        if model not in self.models:
            return _error(
                404,
                f"The model `{model}` does not exist or you do not have access to it.",
            )
        images = sum(
            1
            for m in body.get("messages", [])
            if isinstance(m.get("content"), list)
            for part in m["content"]
            if part.get("type") == "image_url"
        )
        if images > self.max_images:
            return _error(
                400,
                f"Too many images provided. This model supports up to {self.max_images} images",
            )
        why = _not_strict(
            body.get("response_format", {}).get("json_schema", {}).get("schema")
        )
        if why:
            return _error(400, f"Invalid JSON schema for strict mode: {why}")
        return None

    @staticmethod
    def _completion(content: str | None, finish_reason: str) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )
