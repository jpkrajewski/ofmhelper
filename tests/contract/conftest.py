"""Contract tests: one behaviour, checked on the fake and on the real
service. `backend` runs a test twice: "fake" (OFM_FAKE_EXTERNALS, in the
default run, offline) and "real" (marked `live`: only `scripts/test.sh live`
or `deploy`), so a fake that drifts from the service fails here first.
`paid_backend` is the same for a test whose real half spends money at the
service (an Apify run): that half is also marked `paid`, so it runs only by
`scripts/test.sh paid`, never in the deploy gate.

The real half reads the repo's `.env` (the suite otherwise never does):
keys, proxies, the Drive sandbox folder.
"""

from collections.abc import Iterator

import pytest

from ofmhelpers.config import settings as app_settings
from ofmhelpers.config.settings import (
    ApiyiSettings,
    ElyumSettings,
    GDriveSettings,
    HiggsfieldSettings,
    InstagramSettings,
    KieAISettings,
    LinksSettings,
    LLMSettings,
    RunningHubSettings,
    StackedSettings,
    TelegramSettings,
    ViralSettings,
)
from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.ai_providers.apiyi.fake import fake_apiyi
from ofmhelpers.integrations.ai_providers.elyum.fake import fake_elyum
from ofmhelpers.integrations.ai_providers.higgsfield.fake import fake_higgsfield
from ofmhelpers.integrations.ai_providers.kie.fake import fake_kie
from ofmhelpers.integrations.ai_providers.runninghub.fake import fake_runninghub
from ofmhelpers.integrations.apify.fake import fake_apify
from ofmhelpers.integrations.fanvue.fake import fake_fanvue
from ofmhelpers.integrations.gdrive import client as drive_client
from ofmhelpers.integrations.gdrive.fake import _fake_drive
from ofmhelpers.integrations.instagram.fake import fake_instagram
from ofmhelpers.integrations.llm.cerebras.fake import fake_cerebras
from ofmhelpers.integrations.llm.gemini.fake import fake_gemini
from ofmhelpers.integrations.llm.groq.fake import fake_groq
from ofmhelpers.integrations.senders.telegram.fake import fake_telegram
from ofmhelpers.integrations.short_links.fake import _fake_bouncy, _fake_oopsie
from ofmhelpers.integrations.stacked.fake import fake_stacked
from ofmhelpers.media import fake_media

FAKE = "fake"
REAL = "real"
_REAL_SETTINGS = (
    ApiyiSettings,
    ElyumSettings,
    GDriveSettings,
    HiggsfieldSettings,
    InstagramSettings,
    KieAISettings,
    LinksSettings,
    LLMSettings,
    RunningHubSettings,
    StackedSettings,
    TelegramSettings,
    ViralSettings,
)


def _reset_fakes() -> None:
    for cached in (
        outbound._routes,
        fake_instagram,
        _fake_oopsie,
        _fake_bouncy,
        fake_kie,
        fake_higgsfield,
        _fake_drive,
        fake_gemini,
        fake_groq,
        fake_cerebras,
        fake_apify,
        fake_stacked,
        fake_fanvue,
        fake_runninghub,
        fake_elyum,
        fake_apiyi,
        fake_telegram,
        fake_media.mp4,
    ):
        cached.cache_clear()


def use_real_env(monkeypatch) -> None:
    """Keys, proxies and the Drive sandbox from the repo's .env."""
    for cls in _REAL_SETTINGS:
        monkeypatch.setitem(cls.model_config, "env_file", ".env")
    drive_client._credentials.cache_clear()


@pytest.fixture
def real_env(monkeypatch) -> None:
    use_real_env(monkeypatch)


def _switched(which: str, monkeypatch, tmp_path) -> Iterator[str]:
    if which == FAKE:
        monkeypatch.setenv("OFM_FAKE_EXTERNALS", "1")
        monkeypatch.setenv("OFM_FAKES_DIR", str(tmp_path / "fakes"))
    else:
        use_real_env(monkeypatch)
    assert app_settings.infra.fake_externals == (which == FAKE)
    _reset_fakes()
    yield which
    _reset_fakes()


@pytest.fixture(params=[FAKE, pytest.param(REAL, marks=pytest.mark.live)])
def backend(request, monkeypatch, tmp_path) -> Iterator[str]:
    yield from _switched(request.param, monkeypatch, tmp_path)


@pytest.fixture(
    params=[FAKE, pytest.param(REAL, marks=[pytest.mark.live, pytest.mark.paid])]
)
def paid_backend(request, monkeypatch, tmp_path) -> Iterator[str]:
    """`backend` for a test whose real half costs money: only
    `scripts/test.sh paid` runs that half."""
    yield from _switched(request.param, monkeypatch, tmp_path)
