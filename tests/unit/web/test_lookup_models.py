"""The lists the stores hand out in place of id-keyed dicts: each finds its
row by id and says "none" the way its page expects."""

from ofmhelpers.schemas.devices import AccountPhones, Device, PhonesByAccount
from ofmhelpers.schemas.drive import StoredFolder, StoredFolders


def _device(label: str) -> Device:
    return Device(
        id=1,
        user_id=1,
        label=label,
        model_id=None,
        color_id=None,
        custom_model="",
        owner="",
        phone_number="",
        notes="",
        added_at=0,
        updated_at=0,
        retired_at=None,
    )


def test_phones_by_account_finds_an_accounts_phones():
    phones = PhonesByAccount(
        rows=[
            AccountPhones(account_id=7, phones=[_device("A"), _device("B")]),
            AccountPhones(account_id=9, phones=[_device("C")]),
        ]
    )
    assert [d.label for d in phones.of(7)] == ["A", "B"]
    assert [d.label for d in phones.of(9)] == ["C"]


def test_an_account_on_no_phone_has_no_phones():
    assert PhonesByAccount().of(7) == []


def test_stored_folders_finds_a_logins_folder():
    one = StoredFolder(login_id=3, folder_id="f3", name="a - Anna")
    two = StoredFolder(login_id=4, folder_id="f4", name="b - Ben")
    folders = StoredFolders(folders=[one, two])
    assert folders.get(4) == two
    assert folders.get(5) is None
