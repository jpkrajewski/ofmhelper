# Copyright (C) 2026 jpkrajewski

import pytest
from signin import make_user

from ofmhelpers.background import recovery
from ofmhelpers.db.jobs.store import create_job, fail_job, get_job
from ofmhelpers.features.events import Event
from ofmhelpers.features.jobs import notices
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.integrations.ai_providers import tasks
from ofmhelpers.integrations.ai_providers.base import TaskState
from ofmhelpers.integrations.ai_providers.errors import (
    ProviderGenerationError,
    ProviderNoCreditsError,
)
from ofmhelpers.integrations.apify import ApifyCreditError

pytestmark = pytest.mark.filterwarnings("ignore")

KIE_EMPTY = "kie.ai: credits insufficient (code 402)"
HIGGSFIELD_EMPTY = (
    "Your credit balance is too low to complete this request. "
    "Please top up your balance and try again."
)


@pytest.fixture
def sent(monkeypatch) -> list[Event]:
    out: list[Event] = []
    monkeypatch.setattr(notices, "emit", out.append)
    return out


def _failing(exc: Exception) -> str:
    user = make_user("someone@example.com")

    def refused() -> None:
        raise exc

    job_id = create_job("clone", {}, actor=str(user.id))
    run_job(job_id, refused, {})
    return job_id


def test_an_empty_provider_account_is_told_with_who_and_what(sent):
    job_id = _failing(ProviderNoCreditsError(KIE_EMPTY, provider="kie.ai"))

    job = get_job(job_id)
    assert job is not None
    assert job.status == "failed"
    assert len(sent) == 1
    event = sent[0]
    assert event.name == "provider.no_credits"
    assert event.title == "kie.ai: no credits"
    assert event.text.startswith("someone@example.com · clone: ")
    assert "No credits" in event.text
    assert event.url == "/account"


def test_one_message_per_provider_per_hour(sent):
    _failing(ProviderNoCreditsError(KIE_EMPTY, provider="kie.ai"))
    _failing(ProviderNoCreditsError(KIE_EMPTY, provider="kie.ai"))
    _failing(ProviderNoCreditsError("APIYI: insufficient balance.", provider="APIYI"))

    kie, again, apiyi = (event.key for event in sent)
    assert kie == again
    assert apiyi != kie
    assert kie.startswith("provider.no_credits:kie.ai:")


def test_an_empty_apify_account_is_told(sent):
    _failing(ApifyCreditError("No Apify credit left: usage exceeded"))

    assert [event.title for event in sent] == ["Apify: no credits"]


def test_any_other_failure_tells_nothing(sent):
    _failing(ProviderGenerationError("kie.ai failed this generation: nsfw"))
    _failing(RuntimeError("disk full"))

    assert sent == []


def test_recovery_tells_a_request_that_failed_for_want_of_credits(sent, monkeypatch):
    user = make_user("someone@example.com")
    job_id = create_job("clone", {}, actor=str(user.id))
    fail_job(job_id, "Timed out")

    class Empty:
        label = "Higgsfield"

        def status(self, _task_id: str) -> TaskState:
            return TaskState(state="failed", error=HIGGSFIELD_EMPTY, no_credits=True)

    monkeypatch.setattr(recovery, "get_provider", lambda *_a: Empty())
    part = tasks.ProviderTask(
        job_id=job_id,
        provider="higgsfield",
        model="m",
        task_id="t",
        api_key="k",
    )

    assert recovery.settle_job([part], active=None) == "failed"
    assert [event.title for event in sent] == ["Higgsfield: no credits"]
    job = get_job(job_id)
    assert job is not None
    assert "Higgsfield failed this generation" in (job.error or "")
