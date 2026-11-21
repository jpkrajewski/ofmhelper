import json
import re
from http import HTTPStatus

from pydantic import ValidationError

from ofmhelpers.config import settings
from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.x.schema import (
    Answer,
    PostResult,
    UserResult,
    XPost,
    XProfile,
)
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

ROOT = "https://x.com"
LOGGED_OUT_POSTS = 5
_ANSWER = re.compile(r'kind:"Result\.Ok",value:"((?:[^"\\]|\\.)*)"', re.DOTALL)
_JS_ESCAPE = re.compile(r"\\(?:x([0-9a-fA-F]{2})|(.))", re.DOTALL)
_JSON_ESCAPES = frozenset('"\\/bfnrtu')
_JS_ONLY_ESCAPES = frozendict({"v": "\\u000b", "0": "\\u0000", "\n": ""})
_SMALL_AVATAR = "_normal."
_BIG_AVATAR = "_400x400."


class XError(Exception): ...


class XNotFoundError(XError): ...


class XRateLimitedError(XError): ...


class XUnavailableError(XError): ...


def _json_escape(found: re.Match[str]) -> str:
    hex_code, char = found.group(1), found.group(2)
    if hex_code is not None:
        return f"\\u00{hex_code}"
    if char in _JSON_ESCAPES:
        return f"\\{char}"
    return _JS_ONLY_ESCAPES.get(char, char)


def _decoded(js_string: str) -> str:
    text: str = json.loads(f'"{_JS_ESCAPE.sub(_json_escape, js_string)}"', strict=False)
    return text


def embedded_answers(html: str) -> list[Answer]:
    found: list[Answer] = []
    for match in _ANSWER.finditer(html):
        try:
            found.append(Answer.model_validate_json(_decoded(match.group(1))))
        except ValueError, ValidationError:
            logger.warning("x: an embedded answer could not be read", exc_info=True)
    return found


def _user(answers: list[Answer]) -> UserResult | None:
    return next(
        (
            a.data.user.result
            for a in answers
            if a.data.user and a.data.user.result and a.data.user.result.counts
        ),
        None,
    )


def _originals(answers: list[Answer]) -> list[PostResult]:
    return next(
        (
            a.data.user.result.posts()
            for a in answers
            if a.data.user and a.data.user.result and a.data.user.result.originals
        ),
        [],
    )


def _post(username: str, post: PostResult) -> XPost:
    return XPost(
        id=post.rest_id,
        url=f"{ROOT}/{username}/status/{post.rest_id}",
        views=post.views.count,
        posted_at=post.posted_ms / 1000,
        thumbnail_url=next((m.media_url_https for m in post.media), ""),
    )


class XClient:
    def __init__(self, proxy: str | None = None) -> None:
        self._session = outbound.page_session(settings.x, proxy)

    def profile(self, username: str) -> XProfile:
        r = self._session.get(f"{ROOT}/{username}", timeout=settings.x.timeout_s)
        if r.status_code == HTTPStatus.NOT_FOUND:
            msg = f"@{username} is not on X"
            raise XNotFoundError(msg)
        if r.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            msg = "X asked to slow down"
            raise XRateLimitedError(msg)
        if r.status_code >= HTTPStatus.BAD_REQUEST:
            msg = f"X answered {r.status_code}"
            raise XUnavailableError(msg)
        answers = embedded_answers(r.text)
        user = _user(answers)
        if user is None or user.core is None or user.counts is None:
            msg = (
                f"X shows no profile for @{username} (suspended, or X changed its page)"
            )
            raise XError(msg)
        name = user.core.screen_name.lower()
        posts = [_post(name, p) for p in _originals(answers)]
        return XProfile(
            username=name,
            user_id=user.rest_id,
            name=user.core.name,
            followers=user.counts.followers,
            following=user.counts.following,
            is_protected=user.privacy.protected,
            is_verified=user.verification.is_blue_verified,
            avatar_url=user.avatar.image_url.replace(_SMALL_AVATAR, _BIG_AVATAR),
            posts=posts,
            complete=False,
        )
