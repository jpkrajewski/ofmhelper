# Copyright (C) 2026 jpkrajewski

from starlette.responses import JSONResponse, PlainTextResponse, StreamingResponse

from ofmhelpers.web.idempotency import (
    RAW,
    RAW_MAX,
    TOO_LARGE,
    _raw_answer,
    _refusal,
    _stored_body,
    _try_again,
)


def test_a_json_answer_is_kept_as_its_json():
    answer = JSONResponse({"job_id": "a"}, status_code=202)
    assert _stored_body(answer, _raw_answer(answer)) == {"job_id": "a"}


def test_a_plain_answer_is_kept_as_text_with_its_media_type():
    answer = PlainTextResponse("started", status_code=202)
    stored = _stored_body(answer, _raw_answer(answer))
    assert stored == {RAW: {"text": "started", "media_type": "text/plain"}}


def test_an_answer_over_the_cap_is_kept_as_too_large():
    answer = PlainTextResponse("x" * (RAW_MAX + 1))
    stored = _stored_body(answer, _raw_answer(answer))
    assert stored == {RAW: {"text": TOO_LARGE, "media_type": "text/plain"}}


def test_a_streamed_answer_is_kept_empty_with_its_media_type():
    streamed = StreamingResponse(iter([b"{}"]), media_type="application/json")
    assert _raw_answer(streamed) is None
    stored = _stored_body(streamed, None)
    assert stored == {RAW: {"text": "", "media_type": "application/json"}}


def test_refusals_and_try_again_are_told_apart_from_crashes():
    assert _refusal(JSONResponse({"detail": "no"}, status_code=400))
    assert not _refusal(JSONResponse({"detail": "x"}, status_code=500))
    assert _try_again(JSONResponse({"detail": "x"}, status_code=503))
    assert not _try_again(JSONResponse({"detail": "x"}, status_code=500))
