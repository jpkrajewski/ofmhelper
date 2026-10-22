"""Contract tests: one behaviour, checked on the fake and on the real
service. `backend` runs a test twice: "fake" (OFM_FAKE_EXTERNALS, in the
default run, offline) and "real" (marked `live`: only `scripts/test.sh live`
or `deploy`), so a fake that drifts from the service fails here first.

The real half reads the repo's `.env` (the suite otherwise never does):
keys, proxies, the Drive sandbox folder.
"""

from collections.abc import Iterator

import pytest

from ofmhelpers.aigenproviders.higgsfield.fake import fake_higgsfield
from ofmhelpers.aigenproviders.kaiai.fake import fake_kie
from ofmhelpers.config import settings as app_settings
from ofmhelpers.config.settings import (
    GDriveSettings,
    HiggsfieldSettings,
    InstagramSettings,
    KieAISettings,
    LinksSettings,
)
from ofmhelpers.gdrive import client as drive_client
from ofmhelpers.gdrive.fake import _fake_drive
from ofmhelpers.instagram.fake import fake_instagram
from ofmhelpers.links.fake import _fake_bouncy, _fake_oopsie
from ofmhelpers.utils import fake_media, outbound

FAKE = "fake"
REAL = "real"
_REAL_SETTINGS = (
    GDriveSettings,
    HiggsfieldSettings,
    InstagramSettings,
    KieAISettings,
    LinksSettings,
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


@pytest.fixture(params=[FAKE, pytest.param(REAL, marks=pytest.mark.live)])
def backend(request, monkeypatch, tmp_path) -> Iterator[str]:
    if request.param == FAKE:
        monkeypatch.setenv("OFM_FAKE_EXTERNALS", "1")
        monkeypatch.setenv("OFM_FAKES_DIR", str(tmp_path / "fakes"))
    else:
        use_real_env(monkeypatch)
    assert app_settings.infra.fake_externals == (request.param == FAKE)
    _reset_fakes()
    yield request.param
    _reset_fakes()
