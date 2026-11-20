from starlette.responses import JSONResponse, PlainTextResponse, StreamingResponse

from ofmhelpers.web.idempotency import _answer


def test_every_json_answer_but_a_refusal_is_kept():
    assert _answer(JSONResponse({"job_id": "a"}, status_code=202)) == b'{"job_id":"a"}'
    assert _answer(JSONResponse({"detail": "x"}, status_code=500)) == b'{"detail":"x"}'
    assert _answer(JSONResponse({"detail": "no"}, status_code=400)) is None
    assert _answer(PlainTextResponse("ok")) is None
    streamed = StreamingResponse(iter([b"{}"]), media_type="application/json")
    assert _answer(streamed) is None
