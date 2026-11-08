"""kie.ai, Higgsfield, RunningHub, Elyum and APIYI behind the shared interface, on
their fakes and on the real APIs: a wrong key is refused the same way
everywhere (free), and the cheapest job runs end to end (its real side is
marked `paid`: a few cents per provider, run only by `scripts/test.sh paid`;
keys from .env)."""

import pytest
from PIL import Image

from ofmhelpers.integrations.ai_providers.apiyi.fake import FAKE_KEY as APIYI_FAKE_KEY
from ofmhelpers.integrations.ai_providers.apiyi.provider import ApiyiProvider
from ofmhelpers.integrations.ai_providers.catalog import get_spec
from ofmhelpers.integrations.ai_providers.elyum.fake import FAKE_KEY as ELYUM_FAKE_KEY
from ofmhelpers.integrations.ai_providers.elyum.provider import ElyumProvider
from ofmhelpers.integrations.ai_providers.errors import ProviderAuthError
from ofmhelpers.integrations.ai_providers.higgsfield.fake import FAKE_KEY as HF_FAKE_KEY
from ofmhelpers.integrations.ai_providers.higgsfield.provider import HiggsfieldProvider
from ofmhelpers.integrations.ai_providers.kie.fake import FAKE_KEY as KIE_FAKE_KEY
from ofmhelpers.integrations.ai_providers.kie.provider import KieProvider
from ofmhelpers.integrations.ai_providers.runner import generate
from ofmhelpers.integrations.ai_providers.runninghub.fake import FAKE_KEY as RH_FAKE_KEY
from ofmhelpers.integrations.ai_providers.runninghub.provider import RunningHubProvider

BAD_KEY = "contract-test-invalid-key"
PROVIDERS = {
    "kie": (KieProvider, BAD_KEY, KIE_FAKE_KEY),
    "higgsfield": (HiggsfieldProvider, f"{BAD_KEY}:{BAD_KEY}", HF_FAKE_KEY),
    "runninghub": (RunningHubProvider, BAD_KEY, RH_FAKE_KEY),
    "elyum": (ElyumProvider, f"ek_live_{BAD_KEY}", ELYUM_FAKE_KEY),
    "apiyi": (ApiyiProvider, f"sk-{BAD_KEY}", APIYI_FAKE_KEY),
}
# A task id nobody has, in each provider's own format: RunningHub's are
# numeric, and it refuses a UUID with 1007 "failed to parse request body"
# before it looks at the key.
UNKNOWN_TASK = {
    "kie": "00000000-0000-0000-0000-000000000000",
    "higgsfield": "00000000-0000-0000-0000-000000000000",
    "runninghub": "1904152012345678901",
    "elyum": "g_000000000000",
    "apiyi": "00000000-0000-4000-8000-000000000000",
}
# Presumed cheapest model per provider (Lite / Turbo tier); Elyum's
# catalog has video only: Wan 3.0 at its shortest and lowest, $0.08.
CHEAPEST = {
    "kie": "kie:seedream/5-lite-text-to-image",
    "higgsfield": "higgsfield:z-image/turbo",
    "runninghub": "runninghub:seedream-v5-lite/text-to-image",
    "elyum": "elyum:alibaba/wan-3.0/text-to-video",
    "apiyi": "apiyi:wan3.0-video",
}
CHEAPEST_SETTINGS = {
    "elyum": {"resolution": "480p", "duration": 2, "audio": False},
    # APIYI: Wan 3.0 at 480P, 2 s: $0.0588.
    "apiyi": {"resolution": "480P", "duration": 2, "audio": False},
}


@pytest.fixture(params=list(PROVIDERS))
def provider_name(request) -> str:
    return request.param


def test_a_wrong_key_is_refused_on_status(backend, provider_name):
    cls, bad, _ = PROVIDERS[provider_name]
    with pytest.raises(ProviderAuthError, match=f"{cls.label}: wrong API key"):
        cls(bad).status(UNKNOWN_TASK[provider_name])


def test_a_wrong_key_is_refused_at_the_first_upload(backend, provider_name, tmp_path):
    cls, bad, _ = PROVIDERS[provider_name]
    image = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), "teal").save(image)
    with pytest.raises(ProviderAuthError, match=f"{cls.label}: wrong API key"):
        cls(bad).upload(image)


def _cheapest_job(cls, key: str) -> None:
    assert key, f"no {cls.label} key in .env"
    spec = get_spec(CHEAPEST[cls.name])
    payload = {
        "prompt": "a single red apple on white",
        **CHEAPEST_SETTINGS.get(cls.name, {}),
    }
    paths = generate(cls(key), spec, payload, "contract-job")
    assert paths
    assert all(p.exists() and p.stat().st_size > 0 for p in paths)


@pytest.fixture
def fast_polls(monkeypatch):
    monkeypatch.setenv("OFM_AIGEN_POLL_INTERVAL_S", "0.01")
    monkeypatch.setenv("OFM_AIGEN_POLL_MAX_INTERVAL_S", "0.02")
    monkeypatch.setenv("OFM_APIYI_MIN_POLL_INTERVAL_S", "0")


def test_the_cheapest_job_runs_end_to_end_on_the_fake(
    fake_externals, fast_polls, provider_name
):
    cls, _, fake_key = PROVIDERS[provider_name]
    _cheapest_job(cls, fake_key)


@pytest.mark.live
@pytest.mark.paid
def test_the_cheapest_job_runs_end_to_end_for_real(real_env, provider_name):
    """PAID: a few cents per provider. Only `scripts/test.sh paid`."""
    cls, _, _ = PROVIDERS[provider_name]
    _cheapest_job(cls, cls.default_key())
