"""The provider-neutral side of `ofmhelpers.llm`: prompts, media kinds, the
per-use defaults, the registry, setup errors, strict schemas and contact
sheets -- no provider is called."""

from pathlib import Path
from typing import Literal

import pytest
from PIL import Image
from pydantic import BaseModel, Field

from ofmhelpers.config.settings import LLMSettings
from ofmhelpers.llm import (
    LLMConfigError,
    LLMInputError,
    LLMUse,
    MediaKind,
    MediaPart,
    Prompt,
    TextPart,
    get_llm,
    llm_names,
)
from ofmhelpers.llm.chat_completions import strict_schema
from ofmhelpers.llm.fakes import example
from ofmhelpers.llm.frames import contact_sheet
from ofmhelpers.llm.gemini.fake import MODELS
from ofmhelpers.llm.models import Usage
from ofmhelpers.utils import fake_media


def test_a_prompt_keeps_text_and_media_in_order():
    prompt = Prompt.of(
        "Subject:", Path("a/face.jpg"), "Video:", Path("b/clip.mp4"), system="role"
    )

    assert [type(p) for p in prompt.parts] == [TextPart, MediaPart, TextPart, MediaPart]
    assert [(m.kind, m.mime_type) for m in prompt.media] == [
        (MediaKind.IMAGE, "image/jpeg"),
        (MediaKind.VIDEO, "video/mp4"),
    ]
    assert prompt.system == "role"
    assert prompt.temperature is None  # the provider's own default


def test_a_file_that_is_no_media_is_refused():
    with pytest.raises(
        LLMInputError, match="notes.txt is not an image, video or audio"
    ):
        MediaPart.of(Path("notes.txt"))


def test_an_empty_prompt_is_refused():
    with pytest.raises(ValueError, match="at least 1"):
        Prompt(parts=[])


def test_every_provider_is_registered():
    assert llm_names() == ["cerebras", "gemini", "groq"]


def test_the_gemini_fake_serves_every_model_the_default_chain_asks():
    """A model the fake does not serve is a 404, which the AI check skips:
    tests would quietly run a shorter chain than production (review #48)."""
    defaults = LLMSettings.model_construct()  # defaults only: no env, no .env
    asked = {defaults.gemini_model} | {
        model.strip() or defaults.gemini_model
        for name, _, model in (n.partition(":") for n in defaults.video_chain)
        if name.strip().lower() == "gemini"
    }
    assert len(asked) > 1  # the chain names models of its own
    assert asked - MODELS == set()


def test_usage_adds_up_what_was_reported():
    found = Usage.summed(
        [
            Usage(input_tokens=10, output_tokens=2),
            Usage(input_tokens=5, thinking_tokens=7, output_tokens=1),
            Usage(),
        ]
    )
    assert found == Usage(input_tokens=15, thinking_tokens=7, output_tokens=3)
    assert Usage.summed([]) == Usage()


def test_each_kind_of_work_has_its_own_default(monkeypatch):
    for name in ("GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.setenv(name, f"key-{name}")

    video, chat = get_llm(LLMUse.VIDEO), get_llm(LLMUse.CHAT)

    assert (video.name, video.model, video.api_key) == (
        "gemini",
        "gemini-3.6-flash",
        "key-GEMINI_API_KEY",
    )
    assert (chat.name, chat.model) == ("groq", "qwen/qwen3.8-27b")


def test_a_default_may_name_its_model(monkeypatch):
    monkeypatch.setenv("CEREBRAS_API_KEY", "k")
    monkeypatch.setenv("LLM_DEFAULT_VIDEO", "Cerebras:gpt-oss-120b")
    found = get_llm(LLMUse.VIDEO)
    assert (found.name, found.model) == ("cerebras", "gpt-oss-120b")


def test_a_named_provider_overrides_the_default(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    found = get_llm(LLMUse.VIDEO, provider="groq:openai/gpt-oss-20b")
    assert (found.name, found.model) == ("groq", "openai/gpt-oss-20b")


def test_an_unknown_provider_is_a_setup_error(monkeypatch):
    monkeypatch.setenv("LLM_DEFAULT_CHAT", "nope")
    with pytest.raises(LLMConfigError, match="Unknown language-model provider: nope"):
        get_llm(LLMUse.CHAT)


def test_no_configured_key_is_a_setup_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMConfigError, match="Gemini: no API key configured"):
        get_llm(LLMUse.VIDEO)


class _Inner(BaseModel):
    note: str = Field(max_length=40)


class _Answer(BaseModel):
    ok: bool
    count: int
    level: Literal["low", "high"]
    problems: list[str] = Field(default_factory=list, max_length=5)
    inner: _Inner
    maybe: float | None = None
    title: str = "x"  # a field named like a schema keyword


def test_the_fake_answers_the_plainest_value_the_schema_allows():
    found = _Answer.model_validate(example(_Answer.model_json_schema()))
    assert found == _Answer(
        ok=True,
        count=0,
        level="low",
        problems=[],
        inner=_Inner(note=""),
        maybe=0,
        title="",
    )


def test_a_strict_schema_is_closed_inlined_and_all_required():
    found = strict_schema(_Answer.model_json_schema())

    assert "$defs" not in found
    assert found["additionalProperties"] is False
    assert found["required"] == [
        "ok",
        "count",
        "level",
        "problems",
        "inner",
        "maybe",
        "title",
    ]
    inner = found["properties"]["inner"]
    assert inner == {
        "type": "object",
        "properties": {"note": {"type": "string"}},
        "required": ["note"],
        "additionalProperties": False,
    }
    assert found["properties"]["problems"] == {
        "type": "array",
        "items": {"type": "string"},
    }
    assert found["properties"]["title"] == {"type": "string"}
    # Still describes the same answers.
    assert _Answer.model_validate(example(found))


def test_a_contact_sheet_tiles_stills_of_a_video(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(fake_media.mp4())  # 320x568

    sheet = contact_sheet(video, tmp_path / "sheet.jpg", 9, 100, 60)

    with Image.open(sheet) as img:
        assert img.format == "JPEG"
        assert img.size == (300, 3 * 178)  # 3x3 stills, each 100 wide
