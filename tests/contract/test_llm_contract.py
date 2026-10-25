"""Every language-model provider behind `ofmhelpers.llm`, on its fake and
on the real API (free tiers; keys from .env under `live`): an image and a
video are read and answered in the asked-for shape (a video goes to Groq
and Cerebras as a contact sheet), Gemini takes a too-big file through its
upload, and a wrong key and an unknown model fail the same way everywhere.

On the real API the free quota is shared with production (one Gemini
project, 20 requests a day per model): Gemini answers on
`gemini-3.5-flash-lite`, not the model the AI check asks first, with two
real calls a run (an image, the uploaded video); nothing is retried, and a
provider out of quota or down skips with its reason -- that is not the
contract breaking. A provider with no key in .env is skipped."""

from typing import Literal

import pytest
from PIL import Image
from pydantic import BaseModel

from ofmhelpers.llm import (
    LLMAuthError,
    LLMConfigError,
    LLMUnavailableError,
    Prompt,
    get_llm,
    llm_class,
)
from ofmhelpers.llm.cerebras.fake import FAKE_KEY as CEREBRAS_KEY
from ofmhelpers.llm.gemini.fake import FAKE_KEY as GEMINI_KEY
from ofmhelpers.llm.groq.fake import FAKE_KEY as GROQ_KEY
from ofmhelpers.utils import fake_media

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
    """Gemini's model with the most free quota, and no retries: a spent
    quota ends in seconds, not minutes of waiting (both backends alike)."""
    monkeypatch.setenv("OFM_GEMINI_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("OFM_LLM_RETRIES", "0")


@pytest.fixture(params=list(PROVIDERS))
def provider(request, backend, monkeypatch) -> str:
    """The fake's key on the fake; the real one comes from .env (skipped
    when it has none)."""
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
    """`prompt` answered by `provider`; on the real API, out of quota or
    down is a skip with the provider's reason."""
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
