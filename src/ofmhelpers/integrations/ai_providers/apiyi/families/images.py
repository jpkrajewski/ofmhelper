# Copyright (C) 2026 jpkrajewski

from pydantic import Field

from ofmhelpers.config import settings
from ofmhelpers.config.types import ImageLimits
from ofmhelpers.integrations.ai_providers.apiyi.families.dashscope import with_defaults
from ofmhelpers.integrations.ai_providers.apiyi.families.sync import Made, SyncImages
from ofmhelpers.integrations.ai_providers.apiyi.models import PROVIDER, Text
from ofmhelpers.integrations.ai_providers.errors import ProviderUnavailableError
from ofmhelpers.integrations.ai_providers.http import Lenient, parsed
lazy from ofmhelpers.integrations.ai_providers.apiyi.client import ApiyiClient
lazy from ofmhelpers.integrations.ai_providers.spec import Fields, ModelSpec, Payload

GENERATIONS = "/v1/images/generations"
EDITS = "/v1/images/edits"
IMAGES = "image_urls"
_OURS = frozenset({IMAGES, "aspect_ratio"})


class ImageDatum(Lenient):
    url: str | None = None
    b64_json: Text = ""


class ImagesAnswer(Lenient):
    data: list[ImageDatum] = Field(default_factory=list)

    def made(self) -> Made:
        urls = [d.url for d in self.data if d.url and not d.b64_json]
        images = [("image/png", d.b64_json) for d in self.data if d.b64_json]
        if not urls and not images:
            msg = f"{PROVIDER}: the image answer held no image"
            raise ProviderUnavailableError(msg, maybe_made=False)
        return Made(urls=urls, images=images)


def _refs(payload: Payload) -> list[str]:
    refs = payload.get(IMAGES) or []
    return [str(r) for r in (refs if isinstance(refs, list) else [refs]) if r]


def _api_fields(spec: ModelSpec, payload: Payload) -> Fields:
    values = with_defaults(spec, payload)
    return Fields(
        values={
            "model": spec.model_id,
            **{k: v for k, v in values.values.items() if k not in _OURS},
        },
    )


class GptImage(SyncImages):
    prefixes = ("gpt-image",)
    _LIMITS = ImageLimits(max_bytes=10 * 1000 * 1000, max_side=8000)

    def make(self, client: ApiyiClient, spec: ModelSpec, payload: Payload) -> Made:
        official = not spec.model_id.endswith(("-all", "-vip"))
        fields = _api_fields(spec, payload).values
        if not official:
            fields["response_format"] = "b64_json"
        timeout = settings.apiyi.image_timeout_s
        refs = _refs(payload)
        if not refs:
            response = client.submit(GENERATIONS, json=fields, timeout=timeout)
            return parsed(response, ImagesAnswer, PROVIDER).made()
        name = "image[]" if official else "image"
        paths = [client.image_file(r, self._LIMITS) for r in refs]
        handles = [p.open("rb") for p in paths]
        try:
            files = [(name, (p.name, h)) for p, h in zip(paths, handles, strict=True)]
            data = {k: str(v) for k, v in fields.items()}
            response = client.submit(EDITS, data=data, files=files, timeout=timeout)
        finally:
            for handle in handles:
                handle.close()
        return parsed(response, ImagesAnswer, PROVIDER).made()


class Seedream(SyncImages):
    prefixes = ("seedream",)
    _TOTAL_BYTES = 6 * 1000 * 1000

    def make(self, client: ApiyiClient, spec: ModelSpec, payload: Payload) -> Made:
        body = {
            **_api_fields(spec, payload).values,
            "response_format": "url",
            "watermark": False,
        }
        refs = _refs(payload)
        if refs:
            each = max(self._TOTAL_BYTES // len(refs), 500_000)
            limits = ImageLimits(max_bytes=each, max_side=4096)
            body["image"] = [client.data_url(r, limits) for r in refs]
        response = client.submit(
            GENERATIONS,
            json=body,
            timeout=settings.apiyi.image_timeout_s,
        )
        return parsed(response, ImagesAnswer, PROVIDER).made()


class Flux(SyncImages):
    prefixes = ("flux",)
    _LIMITS = ImageLimits(max_bytes=20 * 1000 * 1000, max_side=1920)

    def make(self, client: ApiyiClient, spec: ModelSpec, payload: Payload) -> Made:
        fields = _api_fields(spec, payload).values
        refs = _refs(payload)
        if refs:
            fields.pop("size", None)
            ratio = payload.get("aspect_ratio")
            if ratio:
                fields["aspect_ratio"] = ratio
            for n, ref in enumerate(refs, start=1):
                key = "input_image" if n == 1 else f"input_image_{n}"
                fields[key] = client.data_url(ref, self._LIMITS)
        response = client.submit(
            GENERATIONS,
            json=fields,
            timeout=settings.apiyi.image_timeout_s,
        )
        return parsed(response, ImagesAnswer, PROVIDER).made()
