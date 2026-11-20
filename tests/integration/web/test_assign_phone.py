import pytest
from fastapi.testclient import TestClient
from signin import sign_in_agency

from ofmhelpers.db.devices import store as devices
from ofmhelpers.db.devices.schema import DeviceFields
from ofmhelpers.db.tracked_accounts.store import list_accounts
from ofmhelpers.db.users import store as users
from ofmhelpers.features.phones import service as phones_service
from ofmhelpers.integrations.gdrive.fake import FakeDrive, fake_drive
from ofmhelpers.web.main import app

AGENCY = "agency@example.com"
MY_DRIVE = f"mydrive-{AGENCY}"
ROOT = "agency-root-folder"


@pytest.fixture
def drive(fake_externals, monkeypatch, tmp_path) -> FakeDrive:
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path))
    fake = fake_drive()
    fake.seed_folder("My Drive", item_id=MY_DRIVE, owner=AGENCY, shared=True)
    fake.seed_folder("DEV - ALL ACCOUNTS", MY_DRIVE, item_id=ROOT, owner=AGENCY)
    users._repository().signed_in(users.agency_id(), "", f"fake-refresh:{AGENCY}")
    users.set_drive_root(users.agency_id(), ROOT)
    return fake


@pytest.fixture
def client(drive) -> TestClient:
    c = TestClient(app)
    sign_in_agency(c)
    return c


def _phone(label: str) -> int:
    return devices.add_device(
        DeviceFields(label=label, model_id="iphone-13", color_id="blue", owner="Anna")
    )


def _account(client: TestClient, handle: str) -> int:
    r = client.post("/accounts/add", json={"platform": "instagram", "handles": handle})
    assert r.status_code == 200, r.text
    return next(a.id for a in list_accounts() if a.handle == handle)


def _assign(client: TestClient, phone: int, account: int, **extra):
    return client.post(
        f"/phones/{phone}/accounts", json={"account_id": account, **extra}
    )


def _on(phone: int) -> list[str]:
    device = devices.get_device(phone)
    assert device is not None
    return [x.username for x in device.logins]


def _tracked() -> set[str]:
    return {a.handle for a in list_accounts()}


def test_an_account_with_no_phone_is_assigned_and_gets_its_drive_folder(client, drive):
    phone = _phone("Phone 07")
    account = _account(client, "yuki_kanasiva")
    assert f'href="/phones?assign={account}"' in client.get("/accounts").text

    picking = client.get(f"/phones?assign={account}").text
    assert 'id="ph-pick"' in picking
    assert "data-pickable" in picking

    r = _assign(client, phone, account)

    assert r.status_code == 201, r.text
    assert _on(phone) == ["yuki_kanasiva"]
    accounts = client.get("/accounts").text
    assert f"/phones?assign={account}" not in accounts
    assert f'href="/phones#phone-{phone}"' in accounts
    assert len(drive.named("yuki_kanasiva - Anna", ROOT)) == 1


def test_a_phone_that_has_an_account_refuses_until_replace(client, drive):
    phone = _phone("Phone 07")
    old = _account(client, "sheep_love33")
    assert _assign(client, phone, old).status_code == 201
    new = _account(client, "yuki_kanasiva")

    refused = _assign(client, phone, new)

    assert refused.status_code == 409
    assert refused.json()["detail"] == "Phone 07 already has @sheep_love33"
    assert _on(phone) == ["sheep_love33"]
    assert 'data-has="sheep_love33"' in client.get(f"/phones?assign={new}").text

    replaced = _assign(client, phone, new, replace=True)

    assert replaced.status_code == 201, replaced.text
    assert _on(phone) == ["yuki_kanasiva"]
    assert _tracked() == {"yuki_kanasiva"}
    assert len(drive.named("yuki_kanasiva - Anna", ROOT)) == 1


def test_a_replace_queues_the_drive_sync_once_after_the_old_account_left(
    client, monkeypatch
):
    phone = _phone("Phone 07")
    old = _account(client, "sheep_love33")
    assert _assign(client, phone, old).status_code == 201
    new = _account(client, "yuki_kanasiva")
    steps: list[str] = []
    real_leaving = phones_service.logins_leaving
    real_folder = phones_service._make_drive_folder

    def leaving(*args, **kwargs):
        steps.append("old account leaves")
        return real_leaving(*args, **kwargs)

    def folder(*args, **kwargs):
        steps.append("drive sync queued")
        return real_folder(*args, **kwargs)

    monkeypatch.setattr(phones_service, "logins_leaving", leaving)
    monkeypatch.setattr(phones_service, "_make_drive_folder", folder)

    assert _assign(client, phone, new, replace=True).status_code == 201

    assert steps == ["old account leaves", "drive sync queued"]


def test_a_replaced_account_still_on_another_phone_stays_everywhere(client):
    first, second = _phone("Phone 07"), _phone("Phone 08")
    old = _account(client, "sheep_love33")
    assert _assign(client, first, old).status_code == 201
    assert _assign(client, second, old).status_code == 201
    new = _account(client, "yuki_kanasiva")

    assert _assign(client, first, new, replace=True).status_code == 201

    assert _on(first) == ["yuki_kanasiva"]
    assert _on(second) == ["sheep_love33"]
    assert _tracked() == {"sheep_love33", "yuki_kanasiva"}


def test_an_account_assigned_twice_to_its_phone_is_on_it_once(client, drive):
    phone = _phone("Phone 07")
    account = _account(client, "yuki_kanasiva")
    first = _assign(client, phone, account)

    again = _assign(client, phone, account)

    assert again.status_code == 201, again.text
    assert again.json()["id"] == first.json()["id"]
    assert _on(phone) == ["yuki_kanasiva"]
    assert len(drive.named("yuki_kanasiva - Anna", ROOT)) == 1


def test_a_replace_that_fails_half_way_loses_nothing_and_finishes_when_sent_again(
    client, monkeypatch
):
    phone = _phone("Phone 07")
    old = _account(client, "sheep_love33")
    assert _assign(client, phone, old).status_code == 201
    new = _account(client, "yuki_kanasiva")

    def drive_down(*_args, **_kwargs):
        msg = "Drive is down"
        raise RuntimeError(msg)

    with monkeypatch.context() as broken:
        broken.setattr("ofmhelpers.features.phones.service.logins_leaving", drive_down)
        with pytest.raises(RuntimeError, match="Drive is down"):
            _assign(client, phone, new, replace=True)

    assert sorted(_on(phone)) == ["sheep_love33", "yuki_kanasiva"]
    assert _tracked() == {"sheep_love33", "yuki_kanasiva"}

    again = _assign(client, phone, new, replace=True)

    assert again.status_code == 201, again.text
    assert _on(phone) == ["yuki_kanasiva"]
    assert _tracked() == {"yuki_kanasiva"}


def test_an_unknown_account_or_phone_is_not_found_and_the_page_stays_normal(client):
    phone = _phone("Phone 07")
    account = _account(client, "yuki_kanasiva")

    assert _assign(client, phone, 999_999).status_code == 404
    assert _assign(client, 999_999, account).status_code == 404
    assert _on(phone) == []
    assert 'id="ph-pick"' not in client.get("/phones?assign=999999").text
    assert 'id="ph-pick"' not in client.get("/phones").text


def test_a_retired_phone_takes_no_account(client):
    phone = _phone("Phone 07")
    devices.retire_device(phone)
    account = _account(client, "yuki_kanasiva")

    r = _assign(client, phone, account)

    assert r.status_code == 409
    assert _on(phone) == []


def test_an_instagram_login_added_on_a_phone_gets_its_folder_at_once(client, drive):
    phone = _phone("Phone 07")

    r = client.post(
        f"/phones/{phone}/logins",
        json={"service": "instagram", "username": "yuki_kanasiva"},
    )

    assert r.status_code == 201, r.text
    assert _tracked() == {"yuki_kanasiva"}
    assert len(drive.named("yuki_kanasiva - Anna", ROOT)) == 1
