"""kie.ai and Higgsfield behind the shared interface, on their fakes and on
the real APIs: a wrong key is refused the same way everywhere (free), and
the cheapest job runs end to end (PAID on the real side: a few cents per
provider, `scripts/test.sh live`/`deploy` only; keys from .env)."""

import pytest
from PIL import Image

from ofmhelpers.aigenproviders.catalog import get_spec
from ofmhelpers.aigenproviders.errors import ProviderAuthError
from ofmhelpers.aigenproviders.higgsfield.fake import FAKE_KEY as HF_FAKE_KEY
from ofmhelpers.aigenproviders.higgsfield.provider import HiggsfieldProvider
from ofmhelpers.aigenproviders.kaiai.fake import FAKE_KEY as KIE_FAKE_KEY
from ofmhelpers.aigenproviders.kaiai.provider import KieProvider
from ofmhelpers.aigenproviders.runner import generate

BAD_KEY = "contract-test-invalid-key"
PROVIDERS = {
    "kie": (KieProvider, BAD_KEY, KIE_FAKE_KEY),
    "higgsfield": (HiggsfieldProvider, f"{BAD_KEY}:{BAD_KEY}", HF_FAKE_KEY),
}
# Presumed cheapest image model per provider (Lite / Turbo tier).
CHEAPEST = {
    "kie": "kie:seedream/5-lite-text-to-image",
    "higgsfield": "higgsfield:z-image/turbo",
}


@pytest.fixture(params=list(PROVIDERS))
def provider_name(request) -> str:
    return request.param


def test_a_wrong_key_is_refused_on_status(backend, provider_name):
    cls, bad, _ = PROVIDERS[provider_name]
    with pytest.raises(ProviderAuthError, match=f"{cls.label}: wrong API key"):
        cls(bad).status("00000000-0000-0000-0000-000000000000")


def test_a_wrong_key_is_refused_at_the_first_upload(backend, provider_name, tmp_path):
    cls, bad, _ = PROVIDERS[provider_name]
    image = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), "teal").save(image)
    with pytest.raises(ProviderAuthError, match=f"{cls.label}: wrong API key"):
        cls(bad).upload(image)


def test_the_cheapest_job_runs_end_to_end(
    backend, provider_name, monkeypatch, tmp_path
):
    monkeypatch.setenv("OFM_AIGEN_POLL_INTERVAL_S", "0.01")
    monkeypatch.setenv("OFM_AIGEN_POLL_MAX_INTERVAL_S", "0.02")
    cls, _, fake_key = PROVIDERS[provider_name]
    key = fake_key if backend == "fake" else cls.default_key(admin=True)
    assert key, f"no {cls.label} key in .env"
    spec = get_spec(CHEAPEST[provider_name])

    paths = generate(
        cls(key), spec, {"prompt": "a single red apple on white"}, "contract-job"
    )

    assert paths
    assert all(p.exists() and p.stat().st_size > 0 for p in paths)
