# Copyright (C) 2026 jpkrajewski

from ofmhelpers.background import sweeps
from ofmhelpers.config import settings
from ofmhelpers.config.settings import LinksSettings


def _drive_sweep():
    (sweep,) = [s for s in sweeps.sweeps() if s.name == "drive"]
    return sweep


def test_the_drive_sweep_runs_on_production_only_unless_asked(monkeypatch):
    monkeypatch.setattr(sweeps.storage_jobs, "enabled", lambda: True)
    monkeypatch.delenv("OFM_DRIVE_SWEEP")

    assert _drive_sweep().enabled() is False
    monkeypatch.setenv("OFM_ENV", "production")
    assert _drive_sweep().enabled() is True
    monkeypatch.setenv("OFM_ENV", "development")
    monkeypatch.setenv("OFM_DRIVE_SWEEP", "true")
    assert _drive_sweep().enabled() is True


def test_recovery_restarts_and_senders_follow_the_same_rule(monkeypatch):
    monkeypatch.delenv("OFM_RECOVERY_RESTARTS")
    monkeypatch.delenv("OFM_EVENTS_SENDERS_OFF")

    assert not settings.web.restarting(settings.infra.production)
    assert not settings.events.sending(settings.infra.production)
    assert settings.web.restarting(production=True)
    assert settings.events.sending(production=True)


def test_the_link_sync_spends_provider_limits_on_production_only_unless_asked(
    monkeypatch,
):
    (sweep,) = [s for s in sweeps.sweeps() if s.name == "link sync"]
    monkeypatch.delenv("OFM_LINKS_BACKGROUND_SYNC")

    assert sweep.enabled() is False
    monkeypatch.setenv("OFM_ENV", "production")
    assert sweep.enabled() is True
    monkeypatch.setenv("OFM_ENV", "development")
    monkeypatch.setenv("OFM_LINKS_BACKGROUND_SYNC", "true")
    assert sweep.enabled() is True


def test_a_blank_link_sync_switch_is_unset(monkeypatch):
    monkeypatch.setenv("OFM_LINKS_BACKGROUND_SYNC", "")

    assert LinksSettings().background_sync is None
