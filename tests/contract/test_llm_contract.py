from typing import Literal

import pytest
from PIL import Image
from pydantic import BaseModel

from ofmhelpers.integrations.llm import (
    LLMAuthError,
    LLMConfigError,
    LLMUnavailableError,
    Prompt,
    get_llm,
    llm_class,
)
from ofmhelpers.integrations.llm.cerebras.fake import FAKE_KEY as CEREBRAS_KEY
from ofmhelpers.integrations.llm.gemini.fake import FAKE_KEY as GEMINI_KEY
from ofmhelpers.integrations.llm.groq.fake import FAKE_KEY as GROQ_KEY
from ofmhelpers.media import fake_media

PROVIDERS = {
    "gemini": ("GEMINI_API_KEY", GEMINI_KEY, "Gemini"),
    "groq": ("GROQ_API_KEY", GROQ_KEY, "Groq"),
    "cerebras": ("CEREBRAS_API_KEY", CEREBRAS_KEY, "Cerebras"),
}


class ImageFacts(BaseModel):
    colour: Literal["red", "green", "blue", "other"]


class VideoFacts(BaseModel):
    moving: bool
    seconds: int


@pytest.fixture(autouse=True)
def _spare_the_quota(monkeypatch) -> None:
    monkeypatch.setenv("OFM_GEMINI_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("OFM_LLM_RETRIES", "0")


@pytest.fixture(params=list(PROVIDERS))
def provider(request, backend, monkeypatch) -> str:
    name = request.param
    env, fake_key, _ = PROVIDERS[name]
    if backend == "fake":
        monkeypatch.setenv(env, fake_key)
    elif not llm_class(name).default_key():
        pytest.skip(f"no {env} in .env")
    return name


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(fake_media.mp4())
    return path


def _ask[A: BaseModel](
    backend: str, provider: str, prompt: Prompt, answer: type[A]
) -> A:
    try:
        return get_llm(provider=provider).ask(prompt, answer)
    except LLMUnavailableError as exc:
        if backend != "real":
            raise
        pytest.skip(f"{exc}: {exc.detail[:300]}")


def test_an_image_is_answered_in_the_asked_shape(backend, provider, tmp_path):
    image = tmp_path / "square.png"
    Image.new("RGB", (64, 64), "red").save(image)
    found = _ask(
        backend,
        provider,
        Prompt.of("Which colour fills this image?", image),
        ImageFacts,
    )
    assert isinstance(found, ImageFacts)


def test_a_video_is_answered_in_the_asked_shape(backend, provider, video):
    if backend == "real" and provider == "gemini":
        pytest.skip("Gemini's one real video call is the upload test's")
    found = _ask(
        backend,
        provider,
        Prompt.of("Is anything moving in this video, and how long is it?", video),
        VideoFacts,
    )
    assert isinstance(found.seconds, int)


def test_gemini_takes_a_video_too_big_to_inline_through_an_upload(
    backend, video, monkeypatch
):
    if backend == "fake":
        monkeypatch.setenv("GEMINI_API_KEY", GEMINI_KEY)
    elif not llm_class("gemini").default_key():
        pytest.skip("no GEMINI_API_KEY in .env")
    monkeypatch.setenv("OFM_LLM_INLINE_MAX_BYTES", "1")
    found = _ask(backend, "gemini", Prompt.of("Is anything moving?", video), VideoFacts)
    assert isinstance(found.moving, bool)


def test_a_wrong_key_is_refused(provider):
    label = PROVIDERS[provider][2]
    with pytest.raises(LLMAuthError, match=f"{label}: wrong API key"):
        get_llm(provider=provider, api_key="contract-test-invalid-key").ask(
            Prompt.of("Say hi"), ImageFacts
        )


def test_an_unknown_model_is_a_setup_error(provider):
    with pytest.raises(LLMConfigError):
        get_llm(provider=f"{provider}:no-such-model-0").ask(
            Prompt.of("Say hi"), ImageFacts
        )
