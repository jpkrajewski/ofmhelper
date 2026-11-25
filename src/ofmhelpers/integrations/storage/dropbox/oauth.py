# Copyright (C) 2026 jpkrajewski

import logging
from http import HTTPStatus
from urllib.parse import urlencode

import requests
from dropbox.exceptions import AuthError, DropboxException
from pydantic import ValidationError

from ofmhelpers.config import settings
from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.storage.base import DriveAuthError, DriveError
from ofmhelpers.integrations.storage.dropbox.client import open_api
from ofmhelpers.integrations.storage.dropbox.constants import dropbox_constants
from ofmhelpers.integrations.storage.dropbox.schema import (
    DropboxAccount,
    DropboxGrant,
)

logger = logging.getLogger(__name__)


def authorize_url(redirect_uri: str, state: str, challenge: str) -> str:
    query = urlencode(
        {
            "client_id": settings.dropbox.app_key,
            "response_type": "code",
            "token_access_type": "offline",
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    return f"{dropbox_constants().authorize_url}?{query}"


def _token_call(fields: dict[str, str]) -> requests.Response:
    secret = settings.dropbox.app_secret
    session = outbound.mount(requests.Session())
    return session.post(
        dropbox_constants().token_url,
        data=fields,
        auth=(settings.dropbox.app_key, secret.get_secret_value() if secret else ""),
        timeout=settings.dropbox.interactive_timeout_s,
    )


def exchange(code: str, redirect_uri: str, verifier: str) -> DropboxGrant:
    fields = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    try:
        reply = _token_call(fields)
    except requests.RequestException as exc:
        logger.warning("dropbox token call failed", exc_info=exc)
        raise DriveError(dropbox_constants().no_answer) from exc
    if reply.status_code in (HTTPStatus.BAD_REQUEST, HTTPStatus.UNAUTHORIZED):
        logger.warning("dropbox token call answered %s", reply.status_code)
        raise DriveAuthError(dropbox_constants().reconnect)
    if not reply.ok:
        logger.warning("dropbox token call answered %s", reply.status_code)
        raise DriveError(dropbox_constants().no_answer)
    try:
        return DropboxGrant.model_validate_json(reply.content)
    except ValidationError as exc:
        bad = ", ".join(
            ".".join(str(part) for part in e["loc"]) or "body"
            for e in exc.errors(include_input=False, include_url=False)
        )
        logger.warning("dropbox token reply unreadable: bad fields %s", bad)
        msg = f"{dropbox_constants().no_answer}: unreadable token reply ({bad})"
        raise DriveError(msg) from None


def account(refresh_token: str) -> DropboxAccount:
    try:
        found = open_api(refresh_token, interactive=True).users_get_current_account()
    except AuthError as exc:
        logger.warning("dropbox account read refused", exc_info=exc)
        raise DriveAuthError(dropbox_constants().reconnect) from exc
    except (DropboxException, requests.RequestException) as exc:
        logger.warning("dropbox account read failed", exc_info=exc)
        raise DriveError(dropbox_constants().no_answer) from exc
    return DropboxAccount(
        account_id=found.account_id,
        email=found.email,
        name=found.name.display_name,
    )
