"""What /account does behind its routes (web/routers/account.py): the page
as it shows, a password changed, the person's own API keys saved or removed,
a linked account unlinked. Each write answers a `Notice`: the page again with
a message or an error."""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, SkipValidation

from ofmhelpers.config import settings
from ofmhelpers.db.stores import users
from ofmhelpers.features.links.sync import sync_now
from ofmhelpers.features.refusals import NotFoundError
from ofmhelpers.features.signin import credentials
from ofmhelpers.integrations.ai_providers.base import provider_classes
from ofmhelpers.integrations.short_links.providers import link_providers
from ofmhelpers.integrations.signin import Provider, linkable, providers
from ofmhelpers.schemas import ApiKeysForm, User
from ofmhelpers.schemas.users import LinkedAccount

# How much of a saved key the page shows.
_KEY_TAIL = 4
UNKNOWN_PROVIDER = "Unknown provider"
# What /account says after a provider's consent: the outcome in the query
# string -> its sentence and the page's status.
_LINK_NOTES = {
    "taken": ("That {} account is already linked to someone else.", 409),
    "failed": ("Connecting your {} account failed: try again.", 400),
    "linked": ("{} account linked.", 200),
}


class Notice(BaseModel):
    """What the page says on top after a write, and its status."""

    message: str | None = None
    error: str | None = None
    status_code: int = 200


class PageView(BaseModel):
    """/account: each field is a name `account.html` reads."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    user: User | None
    linked: dict[str, LinkedAccount]
    providers: SkipValidation[list[Provider]]
    # provider -> (label, the saved key's last characters).
    keys: dict[str, tuple[str, str]]
    min_length: int
    message: str | None
    error: str | None


class PasswordChange(BaseModel):
    """`user`: set when the password changed -- the session to keep signed in
    (every other session of theirs is out now)."""

    notice: Notice
    user: User | None = None


class KeyedProvider(BaseModel):
    """A provider a run needs a key for."""

    name: str
    label: str


def keyed_providers() -> list[KeyedProvider]:
    """Generation providers, then link providers (one sharing a name comes
    once, as the link provider)."""
    labels = {
        cls.name: cls.label for cls in provider_classes().all() if cls.needs_key
    } | {cls.key: cls.label() for cls in link_providers().all()}
    return [KeyedProvider(name=name, label=label) for name, label in labels.items()]


def page_view(user_id: int, notice: Notice) -> PageView:
    saved = users.api_keys(user_id)
    return PageView(
        user=users.get_user(user_id),
        linked={a.provider: a for a in users.linked_accounts(user_id)},
        providers=linkable(),
        keys={
            p.name: (p.label, saved.get(p.name)[-_KEY_TAIL:]) for p in keyed_providers()
        },
        min_length=settings.signin.password_min_length,
        message=notice.message,
        error=notice.error,
    )


def link_notice(google: str, apple: str) -> Notice:
    """How connecting an account went, as the callback's redirect says."""
    for label, outcome in (("Google", google), ("Apple", apple)):
        if outcome not in _LINK_NOTES:
            continue
        sentence, status_code = _LINK_NOTES[outcome]
        text = sentence.format(label)
        if outcome == "linked":
            return Notice(message=text)
        return Notice(error=text, status_code=status_code)
    return Notice()


def change_password(
    user_id: int, current: str, password: str, confirm: str
) -> PasswordChange:
    try:
        credentials.change_password(user_id, current, password, confirm)
    except credentials.PasswordRejectedError as exc:
        return PasswordChange(notice=Notice(error=str(exc), status_code=400))
    return PasswordChange(
        notice=Notice(message="Password saved."), user=users.get_user(user_id)
    )


def keys_form(fields: Mapping[str, object]) -> ApiKeysForm:
    """The keys form: its fields come from data (`key_<provider>` per keyed
    provider; a Remove button posts `remove=<provider>`)."""
    known = [p.name for p in keyed_providers()]
    form = ApiKeysForm(
        keys={name: str(fields.get(f"key_{name}", "")) for name in known},
        remove=str(fields.get("remove") or "") or None,
    )
    if form.remove is not None and form.remove not in known:
        raise NotFoundError(UNKNOWN_PROVIDER)
    return form


def save_keys(user_id: int, form: ApiKeysForm) -> Notice:
    """One form, one Save for every provider's key."""
    known = {p.name: p.label for p in keyed_providers()}
    if form.remove is not None:
        users.set_api_key(user_id, form.remove, "")
        return Notice(message=f"{known[form.remove]} key removed.")
    if not form.keys:
        return Notice(error="Nothing to save: paste a key first.", status_code=400)
    for name, key in form.keys.items():
        users.set_api_key(user_id, name, key)
    if set(form.keys) & set(link_providers().names()):
        sync_now(user_id)
    saved = " and ".join(known[name] for name in form.keys)
    return Notice(message=f"{saved} {'key' if len(form.keys) == 1 else 'keys'} saved.")


def unlink(user_id: int, provider: str) -> Notice | None:
    """Stops signing in with that account (and, for Google, Drive uploads as
    it). Refused (a `Notice`) when it is the only way left to sign in."""
    if provider not in providers() or not providers()[provider].linkable:
        raise NotFoundError(UNKNOWN_PROVIDER)
    user = users.get_user(user_id)
    others = [a for a in users.linked_accounts(user_id) if a.provider != provider]
    if user is not None and not user.has_password and not others:
        return Notice(
            error="Set a password or link another account first: this is your only way in.",
            status_code=400,
        )
    users.unlink_identity(user_id, provider)
    return None
