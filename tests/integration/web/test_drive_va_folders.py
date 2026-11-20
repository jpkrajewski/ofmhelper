import pytest
from fastapi.testclient import TestClient
from signin import make_user, sign_in_admin, sign_in_agency

from ofmhelpers.db.stores import drive_folders as store
from ofmhelpers.db.stores import users
from ofmhelpers.features.drive import folders as drive_folders
from ofmhelpers.features.drive import va_folders
from ofmhelpers.integrations.gdrive.fake import FakeDrive, fake_drive
from ofmhelpers.schemas import Role
from ofmhelpers.web.main import app

AGENCY = "agency@example.com"
MY_DRIVE = f"mydrive-{AGENCY}"
ROOT = "agency-root-folder"


@pytest.fixture
def drive(fake_externals) -> FakeDrive:
    fake = fake_drive()
    fake.seed_folder("My Drive", item_id=MY_DRIVE, owner=AGENCY, shared=True)
    fake.seed_folder("DEV - ALL ACCOUNTS", MY_DRIVE, item_id=ROOT, owner=AGENCY)
    users._repository().signed_in(users.agency_id(), "", f"fake-refresh:{AGENCY}")
    users.set_drive_root(users.agency_id(), ROOT)
    return fake


def _add_va(name: str, owner_id: int, email: str = "va@example.com") -> int:
    admin = TestClient(app)
    sign_in_admin(admin)
    r = admin.post(
        "/users",
        data={
            "email": email,
            "name": name,
            "role": "va",
            "owner_id": str(owner_id),
            "password": f"a-password-for-{name}",
        },
    )
    assert r.status_code == 200, r.text
    va = users.by_email(email)
    assert va is not None
    return va.id


def test_a_new_va_gets_a_folder_named_after_them_in_the_owners_root(drive):
    va_id = _add_va("Noelyn", users.agency_id())

    (folder,) = va_folders.of_owner(users.agency_id())
    assert (folder.user_id, folder.name, folder.root_folder_id) == (
        va_id,
        "Noelyn",
        ROOT,
    )
    assert drive.named("Noelyn", ROOT) == [folder.folder_id]


def test_the_sweep_makes_a_missing_folder_once_then_leaves_drive_alone(drive):
    va = make_user("va@example.com", Role.VA)
    users.set_owner(va.id, users.agency_id())

    first = drive_folders.sync()
    drive.clear_calls()
    drive_folders.sync()

    assert first.created == ["va@example.com"]
    assert drive.named("va@example.com", ROOT) != []
    assert drive.writes == []


def test_a_folder_made_by_hand_is_adopted(drive):
    by_hand = drive.seed_folder("Noelyn", ROOT, owner=AGENCY)

    _add_va("Noelyn", users.agency_id())

    (folder,) = va_folders.of_owner(users.agency_id())
    assert folder.folder_id == by_hand
    assert drive.named("Noelyn", ROOT) == [by_hand]


def test_an_owner_without_google_drive_gets_nothing_and_no_error(drive):
    owner = make_user("owner@example.com")

    _add_va("Noelyn", owner.id)
    report = drive_folders.sync(owner.id)

    assert va_folders.of_owner(owner.id) == []
    assert report.failed == []
    assert report.not_connected == ["owner@example.com"]


def test_a_renamed_va_renames_their_folder(drive):
    va_id = _add_va("Noelyn", users.agency_id())
    (old,) = va_folders.of_owner(users.agency_id())
    va = users.get_user(va_id)
    assert va is not None
    renamed = va.model_copy(update={"name": "Noelyn R."})

    folder = va_folders.ensure(drive, ROOT, renamed, old)

    assert folder.folder_id == old.folder_id
    assert drive.named("Noelyn R.", ROOT) == [old.folder_id]


def test_drive_lists_the_vas_by_name_not_among_other_folders(drive):
    _add_va("Noelyn", users.agency_id())
    (folder,) = store.va_folders([u for u in users.list_users() if u.role == Role.VA])
    client = TestClient(app)
    sign_in_agency(client)

    html = client.get("/drive/live").text

    vas = html.split("data-va-folders", 1)[1]
    assert f'data-va-folder="{folder.user_id}" data-folder="Noelyn">' in vas
    assert "Noelyn" in vas
    assert "other folder" not in html
