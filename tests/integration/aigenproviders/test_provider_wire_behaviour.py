"""A whole generation through `runner.generate` with each provider's HTTP
faked at the wire (kie.ai: `responses`; Higgsfield: an httpx transport
under its SDK), bodies from tests/fixtures/providers: success after three
pending polls, 4xx, 5xx, timeout, rate limit, a failed generation, a rerun
after a poll timeout -- what the user is told, what is retried, and that a
request is never submitted (paid for) twice."""

import json
from collections.abc import Iterator

import httpx
import pytest
import requests
import responses
from repo_paths import FIXTURES
from responses import matchers, registries

from ofmhelpers.aigenproviders import tasks
from ofmhelpers.aigenproviders.catalog import get_spec
from ofmhelpers.aigenproviders.errors import (
    ProviderAuthError,
    ProviderGenerationError,
    ProviderInputError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from ofmhelpers.aigenproviders.higgsfield.provider import HiggsfieldProvider
from ofmhelpers.aigenproviders.kaiai.provider import KieProvider
from ofmhelpers.aigenproviders.runner import generate
from ofmhelpers.config import settings

PROVIDER_FIXTURES = FIXTURES / "providers"
KIE_SPEC = get_spec("kie:seedream/5-lite-text-to-image")
HF_SPEC = get_spec("higgsfield:z-image/turbo")
PNG = b"\x89PNG\r\n\x1a\nfake"


def _body(name: str) -> dict:
    return json.loads((PROVIDER_FIXTURES / f"{name}.json").read_text())


@pytest.fixture(autouse=True)
def _fast_polls(monkeypatch):
    monkeypatch.setenv("OFM_AIGEN_POLL_INTERVAL_S", "0.01")
    monkeypatch.setenv("OFM_AIGEN_POLL_MAX_INTERVAL_S", "0.02")


# --- kie.ai --------------------------------------------------------------------


@pytest.fixture
def kie() -> Iterator[responses.RequestsMock]:
    """Answers in the order registered (a URL registered twice answers
    twice)."""
    with responses.RequestsMock(registry=registries.OrderedRegistry) as rsps:
        yield rsps


def _jobs(path: str) -> str:
    return f"{settings.kieai.jobs_base}/{path}"


def _calls(rsps: responses.RequestsMock, path: str) -> int:
    return sum(1 for c in rsps.calls if c.request.url and path in c.request.url)


def _record(rsps, name: str, **kwargs) -> None:
    rsps.get(_jobs("recordInfo"), **({"json": _body(f"kie/{name}")} | kwargs))


def test_kie_three_pending_polls_then_done_saves_the_result_and_pays_once(kie):
    kie.post(
        _jobs("createTask"),
        json=_body("kie/create_ok"),
        match=[
            matchers.header_matcher({"Authorization": "Bearer key"}),
            matchers.json_params_matcher(
                {"model": KIE_SPEC.model_id, "input": {"prompt": "apple"}},
                strict_match=False,
            ),
        ],
    )
    for _ in range(3):
        _record(kie, "record_generating")
    _record(kie, "record_success")
    kie.get("https://tempfile.aiquickdraw.com/results/task_seedream_0001.png", body=PNG)

    (path,) = generate(KieProvider("key"), KIE_SPEC, {"prompt": "apple"}, "job-1")

    assert path.read_bytes() == PNG
    assert _calls(kie, "createTask") == 1
    assert _calls(kie, "recordInfo") == 4
    assert tasks.recall("job-1") is None  # done: nothing left to resume


def test_kie_rejected_input_tells_kies_reason_and_is_not_remembered(kie):
    kie.post(_jobs("createTask"), json=_body("kie/create_rejected"))

    with pytest.raises(
        ProviderInputError, match=r"kie\.ai: prompt is required \(code 422\)"
    ):
        generate(KieProvider("key"), KIE_SPEC, {"prompt": "x"}, "job-1")
    assert tasks.recall("job-1") is None
    assert _calls(kie, "createTask") == 1


def test_kie_wrong_key_says_so(kie):
    kie.post(_jobs("createTask"), json=_body("kie/denied"))
    with pytest.raises(ProviderAuthError) as caught:
        generate(KieProvider("bad"), KIE_SPEC, {"prompt": "x"}, "job-1")
    # What the job shows; kie.ai's own text rides along as a note, for logs.
    assert str(caught.value) == "kie.ai: wrong API key"


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        ({"status": 500, "json": {"code": 500, "msg": "server error"}}, "code 500"),
        (
            {"body": requests.ReadTimeout("read timed out")},
            "kie.ai unreachable: ReadTimeout",
        ),
        ({"json": _body("kie/rate_limited")}, "Rate limited"),
    ],
    ids=["5xx", "timeout", "rate-limited"],
)
def test_kie_create_failing_is_unavailable_and_never_resent(kie, answer, message):
    """A createTask may have reached kie.ai before failing: sending it again
    could pay twice, so it is sent once and the job fails as retryable."""
    kie.post(_jobs("createTask"), **answer)

    with pytest.raises(ProviderUnavailableError, match=message) as caught:
        generate(KieProvider("key"), KIE_SPEC, {"prompt": "x"}, "job-1")
    assert caught.value.retryable
    assert _calls(kie, "createTask") == 1
    assert tasks.recall("job-1") is None


def test_kie_rate_limited_and_failing_polls_are_retried_until_done(kie):
    kie.post(_jobs("createTask"), json=_body("kie/create_ok"))
    _record(kie, "record_generating")
    _record(kie, "rate_limited")
    kie.get(_jobs("recordInfo"), status=503, json={"code": 503, "msg": "busy"})
    kie.get(_jobs("recordInfo"), body=requests.ConnectTimeout("connect timed out"))
    _record(kie, "record_success")
    kie.get("https://tempfile.aiquickdraw.com/results/task_seedream_0001.png", body=PNG)

    (path,) = generate(KieProvider("key"), KIE_SPEC, {"prompt": "x"}, "job-1")

    assert path.read_bytes() == PNG
    assert _calls(kie, "createTask") == 1


def test_kie_failed_generation_tells_the_reason(kie):
    kie.post(_jobs("createTask"), json=_body("kie/create_ok"))
    _record(kie, "record_fail")

    with pytest.raises(
        ProviderGenerationError,
        match="kie.ai failed this generation: .*Content moderation: the prompt was rejected",
    ):
        generate(KieProvider("key"), KIE_SPEC, {"prompt": "x"}, "job-1")
    assert tasks.recall("job-1") is None


def test_kie_rerun_after_a_poll_timeout_resumes_and_never_pays_again(kie, monkeypatch):
    monkeypatch.setenv("OFM_AIGEN_POLL_TIMEOUT_S", "0")
    kie.post(_jobs("createTask"), json=_body("kie/create_ok"))
    with pytest.raises(ProviderTimeoutError, match="still generating"):
        generate(KieProvider("key"), KIE_SPEC, {"prompt": "x"}, "job-1")
    remembered = tasks.recall("job-1")
    assert remembered is not None
    assert remembered.task_id == "task_seedream_0001"

    monkeypatch.setenv("OFM_AIGEN_POLL_TIMEOUT_S", "60")
    _record(kie, "record_success")
    kie.get("https://tempfile.aiquickdraw.com/results/task_seedream_0001.png", body=PNG)
    (path,) = generate(KieProvider("key"), KIE_SPEC, {"prompt": "x"}, "job-1")

    assert path.read_bytes() == PNG
    assert _calls(kie, "createTask") == 1


# --- Higgsfield ------------------------------------------------------------------


class HiggsfieldWire:
    """The SDK's HTTP: answers (method, path) from queued replies, in order;
    a reply may be an exception to raise. Records every request."""

    def __init__(self) -> None:
        self.replies: dict[tuple[str, str], list] = {}
        self.calls: list[httpx.Request] = []

    def queue(self, method: str, path: str, *replies) -> None:
        self.replies.setdefault((method, path), []).extend(replies)

    def count(self, method: str, path: str) -> int:
        return sum(1 for c in self.calls if (c.method, c.url.path) == (method, path))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        queued = self.replies[(request.method, request.url.path)]
        reply = queued.pop(0) if len(queued) > 1 else queued[0]
        if isinstance(reply, Exception):
            raise reply
        status, body = reply
        return httpx.Response(status, json=body)


@pytest.fixture
def hf(monkeypatch) -> HiggsfieldWire:
    wire = HiggsfieldWire()
    transport = httpx.MockTransport(wire)
    real_init = HiggsfieldProvider.__init__

    def init(self, api_key):
        real_init(self, api_key)
        client = self.client
        client.__dict__["_client"] = httpx.Client(
            transport=transport,
            base_url=client.base_url,
            headers={"Authorization": f"Key {api_key}"},
        )

    monkeypatch.setattr(HiggsfieldProvider, "__init__", init)
    return wire


SUBMIT = f"/{HF_SPEC.model_id}"
STATUS = "/requests/11111111-2222-3333-4444-555555555555/status"
HF_RESULT = "https://cloud.higgsfield.ai/results/11111111.png"


def test_higgsfield_three_pending_polls_then_done_saves_the_result_and_pays_once(hf):
    hf.queue("POST", SUBMIT, (200, _body("higgsfield/submit_queued")))
    hf.queue("GET", STATUS, *[(200, _body("higgsfield/status_in_progress"))] * 3)
    hf.queue("GET", STATUS, (200, _body("higgsfield/status_completed")))
    with responses.RequestsMock() as rsps:
        rsps.get(HF_RESULT, body=PNG)
        (path,) = generate(
            HiggsfieldProvider("id:secret"), HF_SPEC, {"prompt": "a"}, "job-1"
        )

    assert path.read_bytes() == PNG
    assert hf.count("POST", SUBMIT) == 1
    assert tasks.recall("job-1") is None


@pytest.mark.parametrize(
    ("reply", "error", "message"),
    [
        (
            (401, _body("higgsfield/denied")),
            ProviderAuthError,
            r"^Higgsfield: wrong API key",
        ),
        (
            (422, {"detail": "prompt: field required"}),
            ProviderInputError,
            "prompt: field required",
        ),
        (httpx.ReadTimeout("read timed out"), ProviderUnavailableError, "Higgsfield"),
    ],
    ids=["401", "422", "timeout"],
)
def test_higgsfield_submit_failing_tells_why_and_remembers_nothing(
    hf, reply, error, message
):
    hf.queue("POST", SUBMIT, reply)
    with pytest.raises(error, match=message):
        generate(HiggsfieldProvider("id:secret"), HF_SPEC, {"prompt": "a"}, "job-1")
    assert tasks.recall("job-1") is None


def test_higgsfield_failing_polls_are_retried_until_done(hf):
    hf.queue("POST", SUBMIT, (200, _body("higgsfield/submit_queued")))
    hf.queue(
        "GET",
        STATUS,
        (200, _body("higgsfield/status_in_progress")),
        httpx.ConnectTimeout("connect timed out"),
        (200, _body("higgsfield/status_completed")),
    )
    with responses.RequestsMock() as rsps:
        rsps.get(HF_RESULT, body=PNG)
        (path,) = generate(
            HiggsfieldProvider("id:secret"), HF_SPEC, {"prompt": "a"}, "job-1"
        )
    assert path.read_bytes() == PNG
    assert hf.count("POST", SUBMIT) == 1


def test_higgsfield_failed_generation_tells_the_reason(hf):
    hf.queue("POST", SUBMIT, (200, _body("higgsfield/submit_queued")))
    hf.queue("GET", STATUS, (200, _body("higgsfield/status_failed")))
    with pytest.raises(ProviderGenerationError, match="bad input image"):
        generate(HiggsfieldProvider("id:secret"), HF_SPEC, {"prompt": "a"}, "job-1")


def test_higgsfield_rerun_after_a_poll_timeout_resumes_and_never_pays_again(
    hf, monkeypatch
):
    monkeypatch.setenv("OFM_AIGEN_POLL_TIMEOUT_S", "0")
    hf.queue("POST", SUBMIT, (200, _body("higgsfield/submit_queued")))
    with pytest.raises(ProviderTimeoutError):
        generate(HiggsfieldProvider("id:secret"), HF_SPEC, {"prompt": "a"}, "job-1")

    monkeypatch.setenv("OFM_AIGEN_POLL_TIMEOUT_S", "60")
    hf.queue("GET", STATUS, (200, _body("higgsfield/status_completed")))
    with responses.RequestsMock() as rsps:
        rsps.get(HF_RESULT, body=PNG)
        generate(HiggsfieldProvider("id:secret"), HF_SPEC, {"prompt": "a"}, "job-1")
    assert hf.count("POST", SUBMIT) == 1
