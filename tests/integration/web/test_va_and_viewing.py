"""The VA role (a few tools, the owner's keys and store, nothing of the
owner's seen) and an admin's "View as" (another person's pages, read only).
Who may open what is `schemas/access.py`; who a request acts as is the
`Viewer` the auth middleware builds (`features/users/viewing.py`)."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from signin import make_user, sign_in, sign_in_admin

from ofmhelpers.db.stores import users
from ofmhelpers.features.account.keys import Keys, keep_used
from ofmhelpers.features.assets.store import assets_root_for_user
from ofmhelpers.features.users import service
from ofmhelpers.features.users.viewing import viewer_for
from ofmhelpers.integrations.ai_providers import tasks
from ofmhelpers.integrations.signin.passwords import hash_password
from ofmhelpers.schemas import Role
from ofmhelpers.schemas.access import Area, areas_of
from ofmhelpers.web.main import app

OWNER = "owner@example.com"
VA = "va@example.com"
OTHER = "other@example.com"
KIE = "kie:nano-banana-pro"

VA_OPENS = ["/home", "/generate", "/clone", "/download-assets", "/edit", "/account"]
VA_REFUSED = [
    "/clone-long",
    "/clone-links",
    "/accounts",
    "/links",
    "/phones",
    "/drive",
    "/viral",
    "/pictures",
    "/reels",
    "/uniqify",
    "/trials",
    "/self-links",
    "/repurpose",
    "/cookies",
    "/users",
    "/chatting",
    "/file-manager",
    "/action-log",
]


def _va_of(owner_email: str = OWNER, va_email: str = VA):
    """An owner and their VA."""
    owner = make_user(owner_email)
    va = make_user(va_email, Role.VA)
    found = users.set_owner(va.id, owner.id)
    assert found is not None
    return owner, found


def _client(email: str, role: Role = Role.USER) -> TestClient:
    client = TestClient(app)
    sign_in(client, email, role)
    return client


# --- the VA role -----------------------------------------------------------------


def test_the_map_gives_a_va_only_their_tools():
    assert set(areas_of(Role.VA)) == {
        Area.SESSION,
        Area.HOME,
        Area.ACCOUNT,
        Area.JOBS,
        Area.REFS,
        Area.GENERATE,
        Area.CLONE,
        Area.DOWNLOAD,
        Area.EDIT,
    }
    assert set(areas_of(Role.ADMIN)) == set(Area)
    assert Area.USERS not in areas_of(Role.USER)


@pytest.mark.parametrize("path", VA_OPENS)
def test_a_va_opens_their_pages(path):
    _va_of()

    assert _client(VA, Role.VA).get(path).status_code == 200


@pytest.mark.parametrize("path", VA_REFUSED)
def test_a_va_is_refused_every_other_page(path):
    _va_of()

    r = _client(VA, Role.VA).get(path, follow_redirects=False)

    assert r.status_code == 403


def test_a_vas_menu_lists_only_what_they_may_open():
    _va_of()

    page = _client(VA, Role.VA).get("/home").text

    for href in ("/generate", "/clone", "/download-assets", "/edit", "/account"):
        assert f'href="{href}"' in page
    for href in ("/clone-links", "/phones", "/drive", "/users", "/cookies"):
        assert f'href="{href}"' not in page


def test_a_va_cannot_save_keys_or_upload_to_drive():
    owner, va = _va_of()
    client = _client(VA, Role.VA)

    assert client.post("/account/keys", data={"key_kie": "k" * 20}).status_code == 403
    assert users.api_keys(va.id).keys == []
    assert users.api_keys(owner.id).keys == []
    r = client.post("/clone/drive-upload", json={"job_id": "x", "logins": [1]})
    assert r.status_code == 403
    assert "API keys" not in client.get("/account").text


def test_a_vas_run_goes_out_on_the_owners_key_which_the_va_never_sees():
    owner, va = _va_of()
    users.set_api_key(owner.id, "kie", "owner-kie-key-1234")

    assert Keys.of(va.id).for_run("kie", KIE, "") == "owner-kie-key-1234"
    # Nothing of it reaches the VA's browser, and the field is not shown.
    page = _client(VA, Role.VA).get("/generate").text
    assert "owner-kie-key-1234" not in page
    assert "data-server-key" in page
    # The owner's own form still has it.
    assert "owner-kie-key-1234" in _client(OWNER).get("/generate").text


def test_a_va_without_an_owner_has_no_key_and_never_an_env_one(monkeypatch):
    monkeypatch.setenv("KIE_AI_API_KEY_ADMIN", "env-admin-key")
    va = make_user(VA, Role.VA)

    assert Keys.of(va.id).for_run("kie", KIE, "") == ""


def test_a_vas_successful_run_saves_no_key():
    owner, va = _va_of()
    made = tasks.ProviderTask(
        job_id="j", provider="kie", model=KIE, task_id="t", api_key="typed-key-99999"
    )

    keep_used(va.id, [made])

    assert users.api_keys(va.id).keys == []
    assert users.api_keys(owner.id).keys == []


def test_a_vas_files_land_in_the_owners_part_of_the_store():
    owner, va = _va_of()
    other = make_user(OTHER)

    assert assets_root_for_user(va.id) == assets_root_for_user(owner.id)
    assert assets_root_for_user(va.id) != assets_root_for_user(other.id)


def test_an_owner_sees_their_vas_jobs_and_a_va_only_their_own():
    owner, va = _va_of()
    other = make_user(OTHER)

    assert viewer_for(owner, None).user_ids == {owner.id, va.id}
    assert viewer_for(va, None).user_ids == {va.id}
    assert viewer_for(other, None).user_ids == {other.id}


# --- /users: making a VA -----------------------------------------------------------


def test_users_adds_a_va_with_an_owner_and_changes_it():
    admin = TestClient(app)
    sign_in_admin(admin)
    owner = make_user(OWNER)
    other = make_user(OTHER)

    r = admin.post(
        "/users",
        data={
            "email": VA,
            "role": "va",
            "owner_id": str(owner.id),
            "password": "a-password-for-the-va",
        },
    )
    assert r.status_code == 200
    va = users.by_email(VA)
    assert va is not None
    assert (va.role, va.owner_id) == (Role.VA, owner.id)

    admin.post(f"/users/{va.id}/owner", data={"owner_id": str(other.id)})
    assert users.get_user(va.id).owner_id == other.id

    # A VA always has an employer: it cannot be cleared.
    r = admin.post(f"/users/{va.id}/owner", data={"owner_id": ""})
    assert r.status_code == 400
    assert users.get_user(va.id).owner_id == other.id
    # The role itself never changes after the user is added.
    r = admin.post(f"/users/{va.id}/role", data={"role": "user"})
    assert r.status_code in (404, 405)
    assert users.get_user(va.id).role == Role.VA


def test_a_va_cannot_own_a_va_and_only_a_va_has_an_owner():
    owner, va = _va_of()
    second = make_user("va2@example.com", Role.VA)

    with pytest.raises(service.RefusedError):
        service.set_owner(second, va.id)
    with pytest.raises(service.RefusedError):
        service.set_owner(owner, va.id)


def test_an_owner_picked_for_a_user_is_ignored():
    """The add form sends an owner only for a VA; one sent anyway for a
    user or an admin makes nobody anyone's."""
    admin = TestClient(app)
    sign_in_admin(admin)
    owner = make_user(OWNER)

    admin.post(
        "/users", data={"email": OTHER, "role": "user", "owner_id": str(owner.id)}
    )

    added = users.by_email(OTHER)
    assert (added.role, added.owner_id) == (Role.USER, None)


# --- an admin's "View as" ------------------------------------------------------------


def _viewing(target_email: str = OWNER, role: Role = Role.USER):
    """An admin's client, viewing as `target_email`."""
    admin = TestClient(app)
    sign_in_admin(admin)
    target = make_user(target_email, role)
    r = admin.post(f"/users/{target.id}/viewing", follow_redirects=False)
    assert r.status_code == 303
    return admin, target


def test_view_as_shows_the_other_persons_pages():
    admin, target = _viewing()
    users.set_api_key(target.id, "kie", "their-kie-key-1234")

    home = admin.get("/home").text
    assert f"Viewing as {target.label}" in home
    assert "Back to my view" in home
    # Their menu, not an admin's; their account, their key's tail only.
    assert 'href="/users"' not in home
    assert admin.get("/users").status_code == 403
    account = admin.get("/account").text
    assert target.email in account
    assert "…1234" in account
    # No key of theirs is ever sent to the admin's browser.
    assert "their-kie-key-1234" not in admin.get("/generate").text


def test_view_as_changes_nothing():
    admin, target = _viewing()

    saved = admin.post("/account/keys", data={"key_kie": "k" * 20})
    assert saved.status_code == 403
    assert "look only" in saved.text
    assert users.api_keys(target.id).keys == []
    fetch = admin.post(
        "/jobs/whatever/hidden",
        json={"hidden": True},
        headers={"sec-fetch-mode": "cors"},
    )
    assert fetch.status_code == 403
    assert fetch.json() == {"detail": f"Viewing as {target.label}: look only"}


def test_back_to_my_view_gives_the_admin_their_own_pages_again():
    admin, _target = _viewing()

    r = admin.post("/viewing/delete", follow_redirects=False)

    assert (r.status_code, r.headers["location"]) == (303, "/users")
    assert admin.get("/users").status_code == 200
    assert "Viewing as" not in admin.get("/home").text


def test_only_an_admin_views_as_and_never_as_themselves_or_someone_gone():
    owner = make_user(OWNER)
    user = _client(OTHER)
    assert user.post(f"/users/{owner.id}/viewing").status_code == 403

    admin = TestClient(app)
    me = sign_in_admin(admin)
    assert admin.post(f"/users/{me.id}/viewing").status_code == 400
    assert admin.post("/users/999999/viewing").status_code == 404
    users.set_active(owner.id, active=False)
    assert admin.post(f"/users/{owner.id}/viewing").status_code == 400


def test_a_person_deactivated_while_viewed_ends_the_view():
    admin, target = _viewing()

    users.set_active(target.id, active=False)

    assert admin.get("/users").status_code == 200
    assert "Viewing as" not in admin.get("/home").text


def test_logging_out_while_viewing_works():
    admin, _target = _viewing()

    r = admin.post("/logout", follow_redirects=False)

    assert (r.status_code, r.headers["location"]) == (303, "/login")


# --- "Login as Virtual Assistant": the employer's email + the VA's password ------

VA_PASSWORD = "va-password-one"


def _va_login(client: TestClient, employer: str, password: str):
    return client.post(
        "/login/va",
        data={"employer": employer, "password": password, "next": "/"},
        follow_redirects=False,
    )


def _big_g(client: TestClient, password: str):
    """Big G Login: start -> the password page -> its callback."""
    started = client.get("/auth/owner/start", follow_redirects=False)
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]
    sent = client.post(
        "/auth/owner/consent",
        data={"state": state, "password": password},
        follow_redirects=False,
    )
    if sent.status_code != 303:
        return sent
    back = urlsplit(sent.headers["location"])
    query = {k: v[0] for k, v in parse_qs(back.query).items()}
    return client.get(back.path, params=query, follow_redirects=False)


def _added_va(password: str = VA_PASSWORD, email: str = VA, employer: str = OWNER):
    """A VA added on /users by an admin, with their employer and password."""
    admin = TestClient(app)
    sign_in_admin(admin)
    owner = make_user(employer)
    r = admin.post(
        "/users",
        data={
            "email": email,
            "role": "va",
            "owner_id": str(owner.id),
            "password": password,
        },
    )
    return admin, r


def test_the_login_page_offers_the_va_login_and_its_page_is_public():
    client = TestClient(app)

    assert 'href="/login/va?next=' in client.get("/login").text
    page = client.get("/login/va")
    assert page.status_code == 200
    assert "Employer email" in page.text


def test_a_va_is_added_with_a_password_and_signs_in_with_the_employers_email():
    _admin, added = _added_va()
    assert added.status_code == 200
    assert f"{OWNER} and the password you set." in added.text
    assert "Login as Virtual Assistant" in added.text
    # No set-password link: there is nothing for the VA to set up.
    assert "/password/" not in added.text
    va = users.by_email(VA)
    assert va is not None
    assert va.has_password

    client = TestClient(app)
    back = _va_login(client, f" {OWNER.upper()} ", VA_PASSWORD)

    assert back.status_code == 303
    assert client.get("/generate").status_code == 200
    assert client.get("/phones", follow_redirects=False).status_code == 403
    assert VA in client.get("/home").text


def test_a_vas_password_opens_no_other_way_in():
    _added_va()
    client = TestClient(app)

    # Not the email form, with the VA's own email or the employer's.
    for email in (VA, OWNER):
        r = client.post(
            "/login", data={"email": email, "password": VA_PASSWORD, "next": "/"}
        )
        assert r.status_code == 401
    # Not Big G Login: that is the admin password only.
    assert _big_g(client, VA_PASSWORD).status_code == 401
    assert client.get("/generate", follow_redirects=False).status_code == 303


def test_the_va_login_takes_no_one_but_a_va():
    """The employer's own password, the admin password, a wrong one, an
    unknown employer, the VA's own email: none opens anything."""
    _added_va()
    owner = users.by_email(OWNER)
    users.set_password_hash(owner.id, hash_password("the-employers-own-pw"))
    client = TestClient(app)

    for employer, password in (
        (OWNER, "the-employers-own-pw"),
        (OWNER, "test-admin"),
        (OWNER, "not-the-password"),
        ("nobody@example.com", VA_PASSWORD),
        (VA, VA_PASSWORD),
    ):
        r = _va_login(client, employer, password)
        assert r.status_code == 401, (employer, password)
        assert "Wrong employer email or password" in r.text
    assert client.get("/generate", follow_redirects=False).status_code == 303


def test_two_employers_vas_may_have_the_same_password_and_each_gets_their_own():
    _added_va()
    _admin, second = _added_va(email="va2@example.com", employer=OTHER)
    assert second.status_code == 200

    first = TestClient(app)
    assert _va_login(first, OWNER, VA_PASSWORD).status_code == 303
    other = TestClient(app)
    assert _va_login(other, OTHER, VA_PASSWORD).status_code == 303

    assert VA in first.get("/home").text
    assert "va2@example.com" in other.get("/home").text


def test_one_employers_vas_never_share_a_password():
    admin, _first = _added_va()
    owner = users.by_email(OWNER)

    again = admin.post(
        "/users",
        data={
            "email": "va2@example.com",
            "role": "va",
            "owner_id": str(owner.id),
            "password": VA_PASSWORD,
        },
    )

    assert again.status_code == 400
    assert "Another VA of this employer has that password" in again.text
    assert users.by_email("va2@example.com") is None


@pytest.mark.parametrize(
    ("data", "said"),
    [
        ({"password": "short"}, "Use at least"),
        ({"password": ""}, "Use at least"),
        ({"password": VA_PASSWORD, "owner_id": ""}, "employer"),
    ],
)
def test_a_va_is_not_added_without_a_real_password_and_an_employer(data, said):
    admin = TestClient(app)
    sign_in_admin(admin)
    owner = make_user(OWNER)
    form = {"email": VA, "role": "va", "owner_id": str(owner.id), **data}

    added = admin.post("/users", data=form)

    assert added.status_code == 400
    assert said in added.text
    assert users.by_email(VA) is None


def test_an_admin_sets_a_vas_new_password_and_the_old_one_stops_working():
    admin, _added = _added_va()
    va = users.by_email(VA)

    r = admin.post(f"/users/{va.id}/password", data={"password": "va-password-two"})

    assert r.status_code == 200
    assert "Password saved." in r.text
    assert _va_login(TestClient(app), OWNER, VA_PASSWORD).status_code == 401
    assert _va_login(TestClient(app), OWNER, "va-password-two").status_code == 303
    # A VA gets no set-password link, a user no admin-set password.
    assert admin.post(f"/users/{va.id}/link").status_code == 400
    owner = users.by_email(OWNER)
    r = admin.post(f"/users/{owner.id}/password", data={"password": "x" * 20})
    assert r.status_code == 400
    assert not users.get_user(owner.id).has_password


def test_a_va_moved_to_another_employer_signs_in_with_that_employers_email():
    admin, _added = _added_va()
    va = users.by_email(VA)
    other = make_user(OTHER)

    admin.post(f"/users/{va.id}/owner", data={"owner_id": str(other.id)})

    assert _va_login(TestClient(app), OWNER, VA_PASSWORD).status_code == 401
    assert _va_login(TestClient(app), OTHER, VA_PASSWORD).status_code == 303


def test_a_deactivated_va_no_longer_signs_in():
    _added_va()
    users.set_active(users.by_email(VA).id, active=False)

    assert _va_login(TestClient(app), OWNER, VA_PASSWORD).status_code == 401


def test_a_va_changes_no_password_and_links_no_account():
    _added_va()
    client = TestClient(app)
    _va_login(client, OWNER, VA_PASSWORD)

    account = client.get("/account").text
    assert 'action="/account/password"' not in account
    assert "/auth/google/start" not in account
    r = client.post(
        "/account/password",
        data={"current": VA_PASSWORD, "password": "y" * 20, "confirm": "y" * 20},
    )
    assert r.status_code == 403
    assert _va_login(TestClient(app), OWNER, VA_PASSWORD).status_code == 303


def test_failed_va_logins_are_braked_like_any_login(monkeypatch):
    monkeypatch.setenv("OFM_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("OFM_LOGIN_MAX_FAILURES", "3")
    _added_va()
    client = TestClient(app)

    codes = [_va_login(client, OWNER, f"wrong-{n}").status_code for n in range(4)]

    assert codes == [401, 401, 401, 429]
    assert _va_login(client, OWNER, VA_PASSWORD).status_code == 429
