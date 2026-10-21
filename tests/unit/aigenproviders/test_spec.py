"""`ModelSpec`: what a catalog entry's input schema turns into -- the form's
fields, its file pickers, typed values, validation and the request body."""

import pytest

from ofmhelpers.aigenproviders.errors import InvalidInputError
from ofmhelpers.aigenproviders.spec import ModelSpec


def _spec(properties, required=("prompt",), fixed=None):
    return ModelSpec(
        provider="p",
        model_id="m",
        label="Model",
        family="F",
        kind="video",
        input_schema={
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        },
        fixed=fixed or {},
    )


URI_LIST = {"type": "array", "items": {"type": "string", "format": "uri"}}

SPEC = _spec(
    {
        "prompt": {"type": "string", "minLength": 1},
        "resolution": {"type": "string", "enum": ["480p", "720p"], "default": "720p"},
        "duration": {"type": "integer", "minimum": 4, "maximum": 15, "default": 5},
        "cfg": {"type": "number"},
        "sound": {"type": "boolean", "default": True},
        "seed": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None},
        "image_urls": {**URI_LIST, "maxItems": 2},
        "video_url": {"type": "string", "format": "uri"},
        "first_frame_url": {"type": "string", "format": "uri"},
        "image_input": {**URI_LIST, "x-media": "image"},
        "link_url": {"type": "string", "format": "uri"},
        "elements": {"type": "array", "items": {"type": "object"}},
        "style": {"type": "object"},
    }
)


def test_media_fields_by_name_format_and_x_media():
    media = {f.name: (f.kind, f.multiple) for f in SPEC.media_fields}

    assert media == {
        "image_urls": ("image", True),
        "video_url": ("video", False),
        "first_frame_url": ("image", False),
        "image_input": ("image", True),
    }


def test_form_fields_are_the_scalars_only():
    """URL fields of no media kind, arrays and objects are left to the
    provider's own default; a nullable anyOf is unwrapped."""
    fields = {f.name: f.type for f in SPEC.form_fields}

    assert fields == {
        "prompt": "string",
        "resolution": "string",
        "duration": "integer",
        "cfg": "number",
        "sound": "boolean",
        "seed": "integer",
    }


def test_parse_form_types_the_strings_and_drops_blanks():
    parsed = SPEC.parse_form(
        {"prompt": "hi", "duration": "7", "cfg": "0.5", "sound": "false", "seed": ""}
    )

    assert parsed == {"prompt": "hi", "duration": 7, "cfg": 0.5, "sound": False}


def test_parse_form_rejects_a_non_number():
    with pytest.raises(InvalidInputError):
        SPEC.parse_form({"duration": "long"})


@pytest.mark.parametrize(
    ("values", "where"),
    [
        ({"resolution": "720p"}, "prompt"),
        ({"prompt": "x", "resolution": "4k"}, "resolution"),
        ({"prompt": "x", "duration": 99}, "duration"),
        ({"prompt": "x", "image_urls": ["a", "b", "c"]}, "image_urls"),
    ],
)
def test_check_names_the_field_that_breaks_the_schema(values, where):
    with pytest.raises(InvalidInputError, match=where):
        SPEC.check(values)


def test_check_passes_a_valid_request_with_local_paths():
    SPEC.check({"prompt": "x", "image_urls": ["/a.png"], "video_url": ["/v.mp4"]})


def test_request_uploads_media_and_sends_fixed_values():
    spec = _spec(SPEC.input_schema["properties"], fixed={"nsfw_checker": False})
    uploaded = []

    def upload(path):
        uploaded.append(path.name)
        return f"https://cdn/{path.name}"

    body = spec.request(
        {
            "prompt": "x",
            "image_urls": ["/a.png", "/b.png"],
            "video_url": ["/v.mp4"],
            "image_input": [],
            "not_in_schema": "dropped",
        },
        upload,
    )

    assert body == {
        "nsfw_checker": False,
        "prompt": "x",
        "image_urls": ["https://cdn/a.png", "https://cdn/b.png"],
        "video_url": "https://cdn/v.mp4",
    }
    assert uploaded == ["a.png", "b.png", "v.mp4"]


def test_key_names_provider_and_model():
    assert SPEC.key == "p:m"
