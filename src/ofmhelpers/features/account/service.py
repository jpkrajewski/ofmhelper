# Copyright (C) 2026 jpkrajewski

lazy from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, SkipValidation

from ofmhelpers.config import settings
from ofmhelpers.db.storage_accounts import store as storage_accounts
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import (
    ApiKeysForm,
    LinkedAccount,
    StorageProvider,
    User,
)
from ofmhelpers.features.account import stacked_keys, telegram
from ofmhelpers.features.account.keys import key_tail
from ofmhelpers.features.links.sync import sync_now
from ofmhelpers.features.refusals import NotFoundError, UnavailableError
from ofmhelpers.features.signin import passwords
from ofmhelpers.features.storage import dropbox_connect
from ofmhelpers.features.users import credentials
from ofmhelpers.integrations.ai_providers.base import provider_classes
from ofmhelpers.integrations.short_links.providers import link_providers
from ofmhelpers.integrations.signin import Provider, linkable, providers
from ofmhelpers.integrations.stacked import (
    StackedAuthError,
    StackedClient,
    StackedError,
    StackedForbiddenError,
)
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

UNKNOWN_PROVIDER = "Unknown provider"
_LINK_NOTES = {
    "taken": ("That {} account is already linked to someone else.", 409),
    "failed": ("Connecting your {} account failed: try again.", 400),
    "linked": ("{} account linked.", 200),
}


class Notice(BaseModel):
    message: str | None = None
    error: str | None = None
    status_code: int = 200


class TelegramCard(BaseModel):
    linked: bool


class DropboxCard(BaseModel):
    email: str | None
    in_use: bool


class TelegramConnect(BaseModel):
    url: str | None = None
    notice: Notice = Notice()


class PageView(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    user: User | None
    linked: dict[str, LinkedAccount]
    providers: SkipValidation[list[Provider]]
    keys: dict[str, tuple[str, str | None]]
    min_length: int
    message: str | None
    error: str | None
    telegram: TelegramCard | None = None
    dropbox: DropboxCard | None = None
    google_in_use: bool = True


class PasswordChange(BaseModel):
    notice: Notice
    user: User | None = None


class KeyedProvider(BaseModel):
    name: str
    label: str


def keyed_providers() -> list[KeyedProvider]:
    labels = {
        slot.id: slot.label
        for cls in provider_classes().values()
        if cls.needs_key
        for slot in cls.key_slots()
    } | {cls.key: cls.label() for cls in link_providers().values()}
    return [KeyedProvider(name=name, label=label) for name, label in labels.items()]


def _stacked() -> KeyedProvider:
    return KeyedProvider(name=stacked_keys.SLOT, label="Stacked")


def keyed_for(*, chats: bool) -> list[KeyedProvider]:
    found = keyed_providers()
    if chats:
        found.append(_stacked())
    return found


def _stacked_refusal(api_key: str) -> str | None:
    try:
        StackedClient(api_key).me()
    except StackedAuthError, StackedForbiddenError:  # silent: shown on the page
        return "Stacked did not accept that key: check it in Stacked."
    except StackedError:
        logger.warning("stacked: key check failed", exc_info=True)
        return "Stacked did not answer: try again in a minute."
    return None


def _dropbox_card(user: User | None) -> DropboxCard | None:
    if user is None or not dropbox_connect.can_connect():
        return None
    account = storage_accounts.get(user.id, StorageProvider.DROPBOX)
    return DropboxCard(
        email=account.email if account else None,
        in_use=user.storage_provider == StorageProvider.DROPBOX,
    )


def page_view(user_id: int, notice: Notice, *, chats: bool) -> PageView:
    saved = users.api_keys(user_id)
    user = users.get_user(user_id)
    return PageView(
        dropbox=_dropbox_card(user),
        google_in_use=user is None or user.storage_provider == StorageProvider.GDRIVE,
        user=user,
        linked={a.provider: a for a in users.linked_accounts(user_id)},
        providers=linkable(),
        keys={
            p.name: (p.label, key_tail(saved.get(p.name) or ""))
            for p in keyed_for(chats=chats)
        },
        min_length=settings.signin.password_min_length,
        message=notice.message,
        error=notice.error,
        telegram=TelegramCard(linked=telegram.linked(user_id))
        if telegram.enabled()
        else None,
    )


def connect_telegram(user_id: int) -> TelegramConnect:
    try:
        return TelegramConnect(url=telegram.connect_url(user_id))
    except UnavailableError as exc:  # silent: logged in connect_url, shown here
        return TelegramConnect(notice=Notice(error=str(exc), status_code=502))


def disconnect_telegram(user_id: int) -> None:
    telegram.unlink(user_id)


def link_notice(google: str, apple: str, dropbox: str = "") -> Notice:
    for label, outcome in (("Google", google), ("Apple", apple), ("Dropbox", dropbox)):
        if outcome not in _LINK_NOTES:
            continue
        sentence, status_code = _LINK_NOTES[outcome]
        text = sentence.format(label)
        if outcome == "linked":
            return Notice(message=text)
        return Notice(error=text, status_code=status_code)
    return Notice()


def change_password(
    user_id: int,
    current: str,
    password: str,
    confirm: str,
) -> PasswordChange:
    try:
        passwords.change_password(user_id, current, password, confirm)
    except credentials.PasswordRejectedError as exc:  # silent: shown on the page
        return PasswordChange(notice=Notice(error=str(exc), status_code=400))
    return PasswordChange(
        notice=Notice(message="Password saved."),
        user=users.get_user(user_id),
    )


def keys_form(fields: Mapping[str, object]) -> ApiKeysForm:
    known = [p.name for p in (*keyed_providers(), _stacked())]
    form = ApiKeysForm(
        keys={name: str(fields.get(f"key_{name}", "")) for name in known},
        remove=str(fields.get("remove") or "") or None,
    )
    if form.remove is not None and form.remove not in known:
        raise NotFoundError(UNKNOWN_PROVIDER)
    return form


def save_keys(user_id: int, form: ApiKeysForm, *, chats: bool) -> Notice:
    known = {p.name: p.label for p in keyed_for(chats=chats)}
    if (set(form.keys) | {form.remove} - {None}) - set(known):
        raise NotFoundError(UNKNOWN_PROVIDER)
    if form.remove is not None:
        users.set_api_key(user_id, form.remove, "")
        return Notice(message=f"{known[form.remove]} key removed.")
    if not form.keys:
        return Notice(error="Nothing to save: paste a key first.", status_code=400)
    typed = form.keys.get(stacked_keys.SLOT)
    if typed is not None and (refused := _stacked_refusal(typed)):
        return Notice(error=refused, status_code=400)
    for name, key in form.keys.items():
        users.set_api_key(user_id, name, key)
    if set(form.keys) & set(link_providers().keys()):
        sync_now(user_id)
    saved = " and ".join(known[name] for name in form.keys)
    return Notice(message=f"{saved} {'key' if len(form.keys) == 1 else 'keys'} saved.")


def unlink(user_id: int, provider: str) -> PasswordChange:
    if provider not in providers() or not providers()[provider].linkable:
        raise NotFoundError(UNKNOWN_PROVIDER)
    user = users.get_user(user_id)
    others = [a for a in users.linked_accounts(user_id) if a.provider != provider]
    if user is not None and not user.has_password and not others:
        return PasswordChange(
            notice=Notice(
                error="Set a password or link another "
                "account first: this is your only way in.",
                status_code=400,
            ),
        )
    users.unlink_identity(user_id, provider)
    users.end_sessions(user_id)
    return PasswordChange(notice=Notice(), user=users.get_user(user_id))
