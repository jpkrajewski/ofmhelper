"""Dev runs on a copy of the prod database (scripts/prod_to_dev.sh) and signs
in with the same Google client: background work that would reach prod's
people is production-only unless dev's .env asks for it (security audit
2026-11-18). The suite turns each on in tests/conftest.py; here they are off
as a dev box has them."""

from ofmhelpers.config import settings
from ofmhelpers.features.jobs import sweeps


def _drive_sweep():
    (sweep,) = [s for s in sweeps.sweeps() if s.name == "drive"]
    return sweep


def test_the_drive_sweep_runs_on_production_only_unless_asked(monkeypatch):
    # Google sign-in set up (fakes are refused in production).
    monkeypatch.setattr(sweeps.drive_folders, "enabled", lambda: True)
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
