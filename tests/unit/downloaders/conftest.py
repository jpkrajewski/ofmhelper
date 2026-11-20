import pytest

from ofmhelpers.integrations import egress


@pytest.fixture(autouse=True)
def hosts_resolve_to_a_public_address(monkeypatch):
    monkeypatch.setattr(egress, "_addresses", lambda _host: ["151.101.1.1"])
