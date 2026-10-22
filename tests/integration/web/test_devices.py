"""/phones: the agency's phones (catalog model and color, drawn) with
their logins as compact rows; one account stored once and linked to every
phone it is on; passwords and recovery codes encrypted at rest and never
in the page -- fetched one login at a time; everyone views, reveals and
exports, only an admin changes anything."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from io import BytesIO

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from pydantic import ValidationError
from signin import sign_in, sign_in_agency
from sqlalchemy import func, select, table, text

from ofmhelpers.phones import (
    ServiceKind,
    catalog,
    find_model,
    get_service,
    services,
)
from ofmhelpers.phones.models import CameraLayout
from ofmhelpers.phones.render import render
from ofmhelpers.web.db.encrypted import VaultError
from ofmhelpers.web.db.repositories import DuplicateError
from ofmhelpers.web.db.session import session_scope
from ofmhelpers.web.main import app
from ofmhelpers.web.schemas import DeviceFields, LoginFields, PhoneQuery
from ofmhelpers.web.stores import devices as store
from ofmhelpers.web.stores.tracked_accounts import list_accounts, track


def _login(password: str) -> TestClient:
    c = TestClient(app)
    c.post("/login", data={"password": password, "next": "/"})
    return c


@pytest.fixture
def admin() -> TestClient:
    return _login("test-admin")


@pytest.fixture
def va() -> TestClient:
    """An admin by Google (sees every phone, the agency's included): the
    page's features. Who sees whose phones: test_ownership.py."""
    c = TestClient(app)
    sign_in_agency(c)
    return c


def _phone(label: str = "Phone 01", **extra) -> int:
    fields = {
        "model_id": "iphone-13",
        "color_id": "blue",
        "owner": "Anna",
    } | extra
    return store.add_device(DeviceFields(label=label, **fields))


def _add_login(device_id: int, service: str, username: str, **extra: str) -> int:
    added = store.add_login(
        device_id, LoginFields(service=service, username=username, **extra)
    )
    assert added is not None
    return added[0]


def _raw_login(login_id: int) -> tuple[str, str]:
    """What Postgres actually holds, past the decrypting column type."""
    with session_scope() as s:
        row = s.execute(
            text("SELECT password, backup_codes FROM logins WHERE id = :i"),
            {"i": login_id},
        ).one()
    return row[0], row[1]


def _count(name: str) -> int:
    with session_scope() as s:
        return s.execute(select(func.count()).select_from(table(name))).scalar_one()


# --- catalog and drawings -----------------------------------------------------


def test_catalog_has_every_family_and_tells_minis_apart():
    families = [f.id for f in catalog().families]
    for family in ("iphone-11", "iphone-12", "iphone-13", "iphone-16", "iphone-17"):
        assert family in families
    mini = find_model("iphone-12-mini")
    regular = find_model("iphone-12")
    assert mini is not None
    assert regular is not None
    assert mini[1].label == "iPhone 12 mini"
    assert regular[1].label == "iPhone 12"
    assert mini[0].id == regular[0].id == "iphone-12"
    assert find_model("iphone-99") is None


def test_colors_are_per_model():
    model = find_model("iphone-11")
    assert model is not None
    names = [c.label for c in catalog().colors(model[1])]
    assert names == ["Black", "White", "Green", "Yellow", "Purple", "(PRODUCT)RED"]
    pro = find_model("iphone-11-pro")
    assert pro is not None
    assert "Midnight Green" in [c.label for c in catalog().colors(pro[1])]


def _model(model_id: str):
    found = find_model(model_id)
    assert found is not None
    return found[1]


def test_every_model_has_the_camera_its_generation_had():
    layouts = {
        "iphone-se-3": CameraLayout.SINGLE_BARE,
        "iphone-xr": CameraLayout.SINGLE_BUMP,
        "iphone-xs": CameraLayout.PILL_X,
        "iphone-11": CameraLayout.DUAL_SQUARE,
        "iphone-12-mini": CameraLayout.DUAL_SQUARE,
        "iphone-13": CameraLayout.DUAL_DIAGONAL,
        "iphone-15-plus": CameraLayout.DUAL_DIAGONAL,
        "iphone-16": CameraLayout.DUAL_PILL,
        "iphone-11-pro": CameraLayout.TRIPLE,
        "iphone-16-pro-max": CameraLayout.TRIPLE,
        "iphone-17-pro": CameraLayout.PLATEAU_TRIPLE,
        "iphone-air": CameraLayout.PLATEAU_SINGLE,
    }
    for model_id, layout in layouts.items():
        assert _model(model_id).camera is layout, model_id
    # LiDAR came with the 12 Pro.
    assert not _model("iphone-11-pro").lidar
    assert _model("iphone-12-pro").lidar


def _lenses(svg: str) -> int:
    """Lenses drawn: each has one pupil (#07070a)."""
    return svg.count('fill="#07070a"')


def test_drawings_differ_by_camera_size_and_color():
    catalog_ = catalog()
    for family in catalog_.families:
        for model in family.models:
            for finish in catalog_.colors(model):
                svg = render(model, finish)
                assert svg.startswith("<svg ")
                assert finish.hex in svg
    lenses = {
        m: _lenses(render(_model(m), None))
        for m in (
            "iphone-se-2",
            "iphone-13",
            "iphone-14-pro",
            "iphone-17-pro",
            "iphone-air",
        )
    }
    assert lenses == {
        "iphone-se-2": 1,
        "iphone-13": 2,
        "iphone-14-pro": 3,
        "iphone-17-pro": 3,
        "iphone-air": 1,
    }
    mini = render(_model("iphone-12-mini"), None)
    regular = render(_model("iphone-12"), None)
    assert 'width="64.20"' in mini
    assert 'width="71.50"' in regular
    assert "iPhone 12 mini back" in mini
    # Not in the catalog: a plain phone, no camera.
    assert _lenses(render(None, None)) == 0


def test_drawing_route_serves_a_cached_svg(va):
    r = va.get("/phones/drawing.svg?model=iphone-12-mini&color=purple")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert "max-age" in r.headers["cache-control"]
    assert catalog().color(_model("iphone-12-mini"), "purple").hex in r.text
    assert va.get("/phones/drawing.svg?model=nope&color=x").status_code == 200


def test_every_service_has_a_hosted_icon():
    for service in services().values():
        assert service.icon_url.startswith("https://api.iconify.design/")
    assert get_service("gmail").icon_url == (
        "https://api.iconify.design/logos/google-gmail.svg"
    )
    assert get_service("x").icon_url.endswith("?color=%23f4f4f8")


def test_phone_form_only_takes_what_the_model_came_in():
    DeviceFields(label="x", model_id="iphone-12-mini", color_id="purple")
    with pytest.raises(ValidationError, match="never came in"):
        DeviceFields(label="x", model_id="iphone-12", color_id="sierra-blue")
    with pytest.raises(ValidationError, match="Unknown model"):
        DeviceFields(label="x", model_id="iphone-99")
    with pytest.raises(ValidationError, match="Pick a model"):
        DeviceFields(label="x", color_id="blue")
    # Off-catalog phone: free-text model, no ids.
    other = DeviceFields(label="x", model_id="", custom_model="Galaxy S24")
    assert (other.model_id, other.custom_model) == (None, "Galaxy S24")


# --- services -----------------------------------------------------------------


def test_services_include_mail_providers_socials_and_custom():
    keys = list(services())
    for key in (
        "icloud",
        "gmail",
        "microsoft",
        "outlook",
        "yahoo",
        "instagram",
        "threads",
        "x",
        "tiktok",
    ):
        assert key in keys
    assert get_service("threads").link("brand") == "https://www.threads.net/@brand"
    assert get_service("threads").username("@Brand") == "brand"
    assert get_service("custom").custom
    assert get_service("custom").kind is ServiceKind.OTHER
    with pytest.raises(ValueError, match="Unknown login service"):
        get_service("myspace")


def test_usernames_are_stored_the_way_the_service_writes_them():
    assert get_service("gmail").username("  Anna.B@Gmail.com ") == "anna.b@gmail.com"
    assert get_service("x").username("@SomeOne") == "someone"
    insta = get_service("instagram")
    assert insta.username("https://www.instagram.com/Yuki_K/") == "yuki_k"
    # A custom site's username may be case-sensitive: only trimmed.
    assert get_service("custom").username("  JohnDoe ") == "JohnDoe"


def test_custom_service_needs_its_provider_name():
    with pytest.raises(ValidationError, match="Name the other"):
        LoginFields(service="custom", username="a")
    body = LoginFields(service="custom", provider=" Shopify ", username="me")
    assert body.provider == "Shopify"
    # Only custom services keep a provider.
    assert LoginFields(service="gmail", provider="x", username="a@b.c").provider == ""


# --- vault --------------------------------------------------------------------


def test_secrets_are_ciphertext_in_the_database_and_absent_from_lists():
    login_id = _add_login(
        _phone(), "instagram", "yuki", password="hunter2", backup_codes="111\n222"
    )
    password, codes = _raw_login(login_id)
    assert "hunter2" not in password
    assert "111" not in codes
    key = os.environ["OFM_VAULT_KEY"]
    assert Fernet(key).decrypt(password.encode()) == b"hunter2"

    listed = store.list_devices()[0].logins[0]
    assert (listed.has_password, listed.has_codes) == (True, True)
    assert (listed.password, listed.backup_codes) == ("", "")
    secrets = store.secrets(login_id)
    assert secrets is not None
    assert (secrets.password, secrets.backup_codes) == ("hunter2", "111\n222")


def test_an_empty_secret_is_stored_empty_and_listed_as_none():
    login_id = _add_login(_phone(), "gmail", "a@b.c")
    assert _raw_login(login_id) == ("", "")
    listed = store.list_devices()[0].logins[0]
    assert (listed.has_password, listed.has_codes) == (False, False)


def test_a_rotated_key_still_reads_old_logins(monkeypatch):
    old = os.environ["OFM_VAULT_KEY"]
    login_id = _add_login(_phone(), "gmail", "a@b.c", password="secret")
    monkeypatch.setenv("OFM_VAULT_KEY", f"{Fernet.generate_key().decode()},{old}")
    secrets = store.secrets(login_id)
    assert secrets is not None
    assert secrets.password == "secret"


def test_a_changed_key_fails_loudly_where_secrets_are_read(monkeypatch, admin):
    login_id = _add_login(_phone(), "gmail", "a@b.c", password="secret")
    monkeypatch.setenv("OFM_VAULT_KEY", Fernet.generate_key().decode())
    with pytest.raises(VaultError, match="do not decrypt"):
        store.secrets(login_id)
    # The list needs no key: the page still renders.
    assert admin.get("/phones").status_code == 200
    r = admin.get(f"/phones/logins/{login_id}/secret")
    assert r.status_code == 500
    assert "do not decrypt with the current OFM_VAULT_KEY" in r.json()["detail"]
    assert admin.get("/phones/export.xlsx").status_code == 500


def test_no_key_saves_nothing(monkeypatch, admin):
    device = _phone()
    monkeypatch.delenv("OFM_VAULT_KEY")
    r = admin.post(
        f"/phones/{device}/logins",
        json={"service": "gmail", "username": "a@b.c", "password": "pw"},
    )
    assert r.status_code == 500
    assert "OFM_VAULT_KEY is not set" in r.json()["detail"]
    assert _count("logins") == 0


def test_a_malformed_key_is_named(monkeypatch):
    device = _phone()
    monkeypatch.setenv("OFM_VAULT_KEY", "nope")
    with pytest.raises(VaultError, match="not a valid Fernet key"):
        _add_login(device, "gmail", "a@b.c", password="pw")


# --- phones -------------------------------------------------------------------


def test_phone_shows_its_catalog_details_and_drawing():
    _phone(model_id="iphone-12-mini", color_id="purple")
    d = store.list_devices()[0]
    assert d.specs == "iPhone 12 mini · Purple"
    assert d.family is not None
    assert d.family.id == "iphone-12"
    assert d.drawing_url == "/phones/drawing.svg?model=iphone-12-mini&color=purple"


def test_off_catalog_phone():
    _phone("Other", model_id=None, color_id=None, custom_model="Pixel 9")
    d = store.list_devices()[0]
    assert d.specs == "Pixel 9"
    assert d.drawing_url == "/phones/drawing.svg?model=&color="


def test_a_label_is_unique():
    _phone()
    with pytest.raises(DuplicateError, match="already exists"):
        _phone()


def test_edit_retire_restore():
    device = _phone()
    _add_login(device, "gmail", "a@b.c")
    fields = DeviceFields(label="Phone 09", model_id="iphone-14", color_id="yellow")
    assert store.update_device(device, fields)
    assert store.retire_device(device)
    d = store.list_devices()[0]
    assert (d.label, d.specs, d.retired, len(d.logins)) == (
        "Phone 09",
        "iPhone 14 · Yellow",
        True,
        1,
    )
    assert store.restore_device(device)
    assert not store.list_devices()[0].retired
    assert not store.update_device(999, fields)


def test_delete_takes_only_the_logins_no_other_phone_has():
    first = _phone()
    second = _phone("Phone 02")
    shared = _add_login(first, "icloud", "both@icloud.com")
    store.add_login(second, LoginFields(service="icloud", username="both@icloud.com"))
    _add_login(first, "gmail", "only@gmail.com")
    assert store.delete_device(first)
    left = store.list_devices()
    assert [d.label for d in left] == ["Phone 02"]
    assert [x.id for x in left[0].logins] == [shared]
    assert _count("logins") == 1
    assert not store.delete_device(first)


# --- logins -------------------------------------------------------------------


def test_one_account_on_two_phones_is_stored_once():
    first = _phone()
    second = _phone("Phone 02")
    login_id = _add_login(first, "gmail", "team@gmail.com", password="pw")
    added = store.add_login(
        second, LoginFields(service="gmail", username="Team@Gmail.com")
    )
    assert added == (login_id, True)
    assert _count("logins") == 1
    devices = store.list_devices()
    assert [x.on for d in devices for x in d.logins] == [
        ["Phone 01", "Phone 02"],
        ["Phone 01", "Phone 02"],
    ]
    assert devices[0].logins[0].elsewhere("Phone 01") == ["Phone 02"]

    # Editing it changes it on both phones.
    store.update_login(
        login_id,
        LoginFields(service="gmail", username="team@gmail.com", password="new"),
    )
    secrets = store.secrets(login_id)
    assert secrets is not None
    assert secrets.password == "new"


def test_linking_never_overwrites_a_different_password():
    first = _phone()
    second = _phone("Phone 02")
    _add_login(first, "gmail", "a@b.c", password="one")
    with pytest.raises(DuplicateError, match="already saved with other password"):
        store.add_login(
            second, LoginFields(service="gmail", username="a@b.c", password="two")
        )
    with pytest.raises(DuplicateError, match="already on this phone"):
        store.add_login(first, LoginFields(service="gmail", username="a@b.c"))
    # A login saved without a password takes the one typed when linked.
    nopw = _add_login(first, "x", "brand")
    store.add_login(second, LoginFields(service="x", username="brand", password="pw"))
    secrets = store.secrets(nopw)
    assert secrets is not None
    assert secrets.password == "pw"


def test_custom_logins_are_told_apart_by_provider():
    device = _phone()
    _add_login(device, "custom", "me", provider="Shopify")
    _add_login(device, "custom", "me", provider="Etsy")
    labels = [x.label for x in store.list_devices()[0].logins]
    assert labels == ["Shopify", "Etsy"]


def test_removing_a_login_keeps_it_where_it_is_still_used():
    first = _phone()
    second = _phone("Phone 02")
    login_id = _add_login(first, "gmail", "a@b.c")
    store.add_login(second, LoginFields(service="gmail", username="a@b.c"))
    assert store.remove_login(first, login_id)
    assert _count("logins") == 1
    assert store.remove_login(second, login_id)
    assert _count("logins") == 0
    assert not store.remove_login(second, login_id)
    assert store.add_login(999, LoginFields(service="gmail", username="z@b.c")) is None


def test_instagram_login_links_to_its_tracked_account():
    track("instagram", "yuki")
    _add_login(_phone(), "instagram", "@Yuki")
    _add_login(_phone("Phone 02"), "instagram", "other")
    tracked = next(a.id for a in list_accounts() if a.handle == "yuki")
    linked = {d.label: d.logins[0].tracked_account_id for d in store.list_devices()}
    assert linked == {"Phone 01": tracked, "Phone 02": None}


def test_secrets_never_reach_the_redis_cache():
    from ofmhelpers.cache import get_redis

    login_id = _add_login(_phone(), "gmail", "a@b.c", password="redis-must-not-see")
    store.list_devices()
    store.secrets(login_id)
    store.export_devices()
    redis = get_redis()
    cached = [k for k in redis.scan_iter("cache:*") if redis.type(k) == b"string"]
    # The reads above did go through the cache: there is something to check.
    assert cached
    assert not [k for k in cached if b"redis-must-not-see" in (redis.get(k) or b"")]


# --- filters and sort ---------------------------------------------------------


def test_query_filters_and_sorts():
    a = _phone("A", model_id="iphone-12", color_id="green", owner="Bo")
    _phone("B", model_id="iphone-15", color_id="pink", owner="Cy")
    _add_login(a, "gmail", "a@b.c")
    _add_login(a, "x", "brand")
    devices = store.list_devices()

    def labels(q: PhoneQuery) -> list[str]:
        return [d.label for d in q.ordered([d for d in devices if q.matches(d)])]

    assert labels(PhoneQuery(family="iphone-12")) == ["A"]
    assert labels(PhoneQuery(color="Pink")) == ["B"]
    assert labels(PhoneQuery(owner="Cy")) == ["B"]
    assert labels(PhoneQuery(service="x")) == ["A"]
    assert labels(PhoneQuery(sort="model")) == ["B", "A"]
    assert labels(PhoneQuery(sort="logins")) == ["A", "B"]
    assert labels(PhoneQuery(sort="")) == ["A", "B"]
    assert PhoneQuery(owner="Bo").filtered


# --- routes -------------------------------------------------------------------


def test_page_lists_phones_without_any_secret(va):
    device = _phone(model_id="iphone-12-mini", color_id="purple")
    _add_login(device, "gmail", "a@b.c", password="pw-123", backup_codes="CODE-999")
    html = va.get("/phones").text
    assert 'href="/phones"' in html
    assert "Phone 01" in html
    assert "iPhone 12 mini · Purple" in html
    assert "/phones/drawing.svg?model=iphone-12-mini&amp;color=purple" in html
    assert "https://api.iconify.design/logos/google-gmail.svg" in html
    assert "a@b.c" in html
    assert "1 recovery-code set" in html
    assert "pw-123" not in html
    assert "CODE-999" not in html
    # One copy button for the whole login, one Show for password + codes;
    # every icon says what it does to a screen reader.
    assert 'aria-label="Copy login:password:codes (' in html
    assert 'aria-label="Show password and recovery codes (' in html
    assert "ph-copy-secret" not in html
    assert "ph-codes-toggle" not in html
    # The viewer's own phones: they edit them.
    assert 'id="ph-add-open"' in html


def test_admin_page_has_dialogs_menu_and_catalog(admin):
    _phone()
    html = admin.get("/phones").text
    assert 'id="ph-dialog"' in html
    assert 'id="ph-login-dialog"' in html
    assert 'id="ph-catalog"' in html
    assert "iphone-12-mini" in html
    for action in ("ph-edit", "ph-retire", "ph-delete", "ph-add-login"):
        assert action in html


def test_page_filters_by_query_string(va):
    _phone("A", model_id="iphone-12", color_id="green")
    _phone("B", model_id="iphone-15", color_id="pink")
    html = va.get("/phones?family=iphone-15").text
    assert 'data-search="b ' in html
    assert 'data-search="a ' not in html
    assert "2 phones" in html
    assert "No phone matches these filters" in va.get("/phones?owner=nobody").text


def test_many_logins_fold_behind_more(va):
    """Two rows show (iCloud + Instagram); the rest fold behind "N more"."""
    device = _phone()
    for i in range(5):
        _add_login(device, "custom", f"user{i}", provider=f"Site {i}")
    html = va.get("/phones").text
    assert html.count("data-extra hidden") == 3
    assert 'data-more="3"' in html


def test_two_logins_have_no_more_button(va):
    device = _phone()
    _add_login(device, "icloud", "a@icloud.com")
    _add_login(device, "instagram", "ig_user")
    html = va.get("/phones").text
    assert 'class="ph-more"' not in html
    assert "No logins yet" not in html


def test_phone_without_logins_says_so(va):
    _phone()
    html = va.get("/phones").text
    assert "No logins yet" in html
    assert 'class="ph-more"' not in html


def test_logins_ordered_icloud_instagram_gmail_first(va):
    device = _phone()
    _add_login(device, "threads", "t_user")
    _add_login(device, "gmail", "a@gmail.com")
    _add_login(device, "instagram", "ig_user")
    _add_login(device, "x", "x_user")
    _add_login(device, "icloud", "a@icloud.com")
    logins = store.list_devices()[0].logins
    assert [x.service for x in logins[:3]] == ["icloud", "instagram", "gmail"]
    # Rest keep link order.
    assert [x.service for x in logins[3:]] == ["threads", "x"]
    html = va.get("/phones").text
    positions = [
        html.index(name) for name in ("a@icloud.com", "ig_user", "a@gmail.com")
    ]
    assert positions == sorted(positions)


def test_empty_page(va):
    assert "No phones yet" in va.get("/phones").text


def test_secret_route_is_for_everyone_and_logged(va, caplog):
    login_id = _add_login(_phone(), "gmail", "a@b.c", password="pw", backup_codes="c1")
    with caplog.at_level("INFO", logger="ofmhelpers.web.routers.inventory.phones"):
        r = va.get(f"/phones/logins/{login_id}/secret")
    assert r.status_code == 200
    assert r.json() == {"password": "pw", "backup_codes": "c1"}
    assert r.headers["cache-control"] == "no-store"
    assert f"Login {login_id} revealed by admin" in caplog.text
    assert "pw" not in caplog.text.replace("phones", "")
    assert va.get("/phones/logins/999/secret").status_code == 404


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/phones/add", {"label": "x"}),
        ("/phones/{d}", {"label": "x"}),
        ("/phones/{d}/retire", None),
        ("/phones/{d}/restore", None),
        ("/phones/{d}/delete", None),
        ("/phones/{d}/logins", {"service": "gmail", "username": "a@b.c"}),
        ("/phones/logins/{l}", {"service": "gmail", "username": "a@b.c"}),
        ("/phones/{d}/logins/{l}/delete", None),
    ],
)
def test_a_user_changes_nothing_of_anothers_phone(path, body):
    """The agency's phone (an admin's): a user reaches none of it."""
    device = _phone()
    login_id = _add_login(device, "icloud", "keep@icloud.com")
    user = TestClient(app)
    sign_in(user)
    r = user.post(path.format(d=device, l=login_id), json=body)
    if path == "/phones/add":
        assert r.status_code == 200  # their own new phone
    else:
        assert r.status_code == 404
    d = next(d for d in store.list_devices() if d.label == "Phone 01")
    assert (d.retired, len(d.logins)) == (False, 1)


def test_admin_flow(admin):
    r = admin.post(
        "/phones/add",
        json={
            "label": " Phone 05 ",
            "model_id": "iphone-12-mini",
            "color_id": "purple",
        },
    )
    assert r.status_code == 200, r.text
    device = r.json()["id"]
    assert admin.post("/phones/add", json={"label": "Phone 05"}).status_code == 409
    bad = admin.post(
        "/phones/add", json={"label": "y", "model_id": "iphone-12", "color_id": "gold"}
    )
    assert bad.status_code == 422
    assert "never came in" in bad.text

    r = admin.post(
        f"/phones/{device}/logins",
        json={"service": "x", "username": "@Brand", "password": "=1+1"},
    )
    assert r.json() == {"id": r.json()["id"], "linked": False}
    login_id = r.json()["id"]
    dup = admin.post(
        f"/phones/{device}/logins", json={"service": "x", "username": "brand"}
    )
    assert dup.status_code == 409
    assert "already on this phone" in dup.json()["detail"]
    bad = admin.post(f"/phones/{device}/logins", json={"service": "?", "username": "a"})
    assert bad.status_code == 422

    edited = admin.post(
        f"/phones/{device}",
        json={"label": "Phone 06", "model_id": "iphone-12-mini", "color_id": "purple"},
    )
    assert edited.status_code == 200
    r = admin.post(
        f"/phones/logins/{login_id}",
        json={"service": "x", "username": "brand", "password": "new"},
    )
    assert r.status_code == 200
    assert admin.post(f"/phones/{device}/retire").status_code == 200
    d = store.list_devices()[0]
    assert (d.label, d.retired) == ("Phone 06", True)
    assert admin.get(f"/phones/logins/{login_id}/secret").json()["password"] == "new"

    assert admin.post(f"/phones/{device}/logins/{login_id}/delete").status_code == 200
    assert admin.post(f"/phones/{device}/delete").status_code == 200
    assert store.list_devices() == []
    assert admin.post("/phones/999/retire").status_code == 404
    assert admin.post("/phones/999/delete").status_code == 404
    assert admin.post("/phones/999/logins/1/delete").status_code == 404
    missing = admin.post("/phones/999/logins", json={"service": "x", "username": "z"})
    assert missing.status_code == 404


# --- export -------------------------------------------------------------------


def _workbook(client: TestClient):
    r = client.get("/phones/export.xlsx")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert r.headers["content-disposition"].startswith('attachment; filename="phones-')
    assert r.headers["cache-control"] == "no-store"
    return load_workbook(BytesIO(r.content))


def _rows(sheet) -> list[dict]:
    values = list(sheet.iter_rows(values_only=True))
    return [dict(zip(values[0], row, strict=True)) for row in values[1:]]


def test_va_exports_every_active_phone_with_its_logins(va):
    device = _phone(phone_number="+1 555 0100")
    _add_login(device, "icloud", "p1@icloud.com", password="ic-pw")
    _add_login(device, "gmail", "one@gmail.com", password="g1")
    _add_login(device, "gmail", "two@gmail.com", password="g2")
    _add_login(device, "instagram", "yuki", password="=SUM(A1)", backup_codes="1\n2")
    _add_login(device, "custom", "me", provider="Shopify", password="s")
    gone = _phone("Phone 99")
    _add_login(gone, "gmail", "old@gmail.com")
    store.retire_device(gone)

    book = _workbook(va)
    assert book.sheetnames == ["Phones", "Logins"]

    phones = _rows(book["Phones"])
    assert len(phones) == 1
    row = phones[0]
    assert row["Phone"] == "Phone 01"
    assert row["Phone number"] == "+1 555 0100"
    assert (row["Model"], row["Color"], row["Owner"]) == ("iPhone 13", "Blue", "Anna")
    assert row["iCloud"] == "p1@icloud.com"
    assert row["iCloud password"] == "ic-pw"
    assert row["Gmail"] == "one@gmail.com\ntwo@gmail.com"
    assert row["Gmail password"] == "g1\ng2"
    assert row["Instagram backup codes"] == "1\n2"
    # A password starting with "=" stays text, never an Excel formula.
    assert row["Instagram password"] == "=SUM(A1)"
    assert row["Other"] == "Shopify: me"
    # Only services some phone uses get columns.
    assert "Yahoo" not in row

    logins = _rows(book["Logins"])
    assert [(x["Service"], x["Username"]) for x in logins] == [
        # Service order, as on the cards.
        ("iCloud", "p1@icloud.com"),
        ("Instagram", "yuki"),
        ("Gmail", "one@gmail.com"),
        ("Gmail", "two@gmail.com"),
        ("Shopify", "me"),
    ]
    assert {x["Phone"] for x in logins} == {"Phone 01"}


def test_export_with_no_phones_still_has_both_headers(va):
    book = _workbook(va)
    assert book["Phones"]["A1"].value == "Phone"
    assert book["Logins"]["F1"].value == "Service"


def test_export_logs_who_and_how_many_never_a_value(va, caplog):
    _add_login(_phone(), "gmail", "a@b.c", password="log-must-not-see")
    with caplog.at_level("INFO", logger="ofmhelpers.web.routers.inventory.phones"):
        _workbook(va)
    assert "Phones export by admin: 1 phones, 1 logins" in caplog.text
    assert "log-must-not-see" not in caplog.text
    assert "a@b.c" not in caplog.text


def test_phones_by_account_maps_active_phones_signed_in(va):
    track("instagram", "yuki")
    track("instagram", "nobody")
    on = _phone("Phone 01", owner="Anna")
    also = _phone("Phone 02", owner="Ben")
    gone = _phone("Phone 99")
    for device in (on, also, gone):
        _add_login(device, "instagram", "yuki")
    _add_login(on, "icloud", "a@icloud.com")
    store.retire_device(gone)
    accounts = list_accounts()
    by_handle = {a.handle: a.id for a in accounts}
    found = store.phones_by_account(accounts)
    assert [d.label for d in found[by_handle["yuki"]]] == ["Phone 01", "Phone 02"]
    assert by_handle["nobody"] not in found


def test_accounts_page_shows_the_phone_in_place_of_notes(va):
    track("instagram", "yuki")
    track("instagram", "nobody")
    device = _phone("Phone 01", owner="Anna", model_id="iphone-12", color_id="green")
    _add_login(device, "instagram", "yuki")
    html = va.get("/accounts").text
    assert '<th scope="col">Phone</th>' in html
    assert "accounts-notes" not in html
    assert f'href="/phones#phone-{device}"' in html
    assert "/phones/drawing.svg?model=iphone-12&amp;color=green" in html
    assert "<span>Anna</span>" in html
    # The other row has no phone.
    assert html.count("data-no-phone") == 1
    # The phone card is the link's anchor.
    assert f'id="phone-{device}"' in va.get("/phones").text
