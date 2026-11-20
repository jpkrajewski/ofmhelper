import json
import mimetypes
import random
import tempfile
import time
import uuid
from collections.abc import Iterable
from http import HTTPStatus
from http.cookiejar import Cookie, MozillaCookieJar
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests
from pydantic import ValidationError

from ofmhelpers.config import settings
from ofmhelpers.errors import AppError
from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.downloaders.cookies import load_jar
from ofmhelpers.integrations.pinterest.models import (
    CreatedPin,
    Envelope,
    PinCreate,
    PinDraft,
    PinterestAccount,
    PinterestBoard,
    ResourceError,
    UploadSlot,
    UploadState,
)
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

SOURCE_URL = "/pin-creation-tool/"
_PWS_HANDLER = "www/pin-creation-tool.js"
LOGIN_COOKIE = "_pinterest_sess"
DOMAIN = "pinterest.com"


class PinterestError(AppError):
    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


class PinterestRateLimitedError(PinterestError):
    def __init__(self, retry_after: float | None) -> None:
        super().__init__("Pinterest rate limited this account: waiting")
        self.retry_after = retry_after


class PinterestMediaError(PinterestError):
    def __init__(self, message: str) -> None:
        super().__init__(message, permanent=True)


class PinterestLoggedOutError(PinterestError):
    def __init__(self) -> None:
        super().__init__(
            "Pinterest logged this account out: upload its cookies again",
            permanent=True,
        )


def _is_pinterest(domain: str) -> bool:
    host = domain.lower().lstrip(".")
    return host == DOMAIN or host.endswith(f".{DOMAIN}")


def only_pinterest(cookies: str) -> str:
    return _as_txt(c for c in load_jar(cookies) if _is_pinterest(c.domain))


def session_expires_at(cookies: str) -> float | None:
    found = [
        c.expires for c in load_jar(cookies) if c.name == LOGIN_COOKIE and c.expires
    ]
    return float(max(found)) if found else None


def _as_txt(cookies: Iterable[Cookie]) -> str:
    keep = MozillaCookieJar()
    for cookie in cookies:
        keep.set_cookie(cookie)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "cookies.txt"
        keep.save(str(path), ignore_discard=True, ignore_expires=True)
        return path.read_text(encoding="utf-8")


def _sent_to(cookie: Cookie, host: str) -> bool:
    domain = cookie.domain.lower()
    if domain.startswith("."):
        return host == domain[1:] or host.endswith(domain)
    return host == domain


def _host(jar: list[Cookie], default: str) -> str:
    hosts = [
        c.domain.lower()
        for c in jar
        if c.name == "csrftoken" and not c.domain.startswith(".")
    ]
    local = [h for h in hosts if h.endswith(f".{DOMAIN}") and h != default]
    return local[0] if local else default


class PinterestClient:
    def __init__(self, cookies: str) -> None:
        s = settings.pinterest
        jar = list(load_jar(cookies))
        host = _host(jar, urlsplit(s.base_url).hostname or "www.pinterest.com")
        self._base = f"https://{host}"
        self._session = outbound.mount(requests.Session())
        mine = sorted(
            (c for c in jar if _sent_to(c, host)), key=lambda c: len(c.domain)
        )
        for cookie in mine:
            self._session.cookies.set_cookie(cookie)
        self._host = host
        self._session.headers.update(
            {
                "User-Agent": s.user_agent,
                "Accept": "application/json, text/javascript, */*, q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "X-Pinterest-PWS-Handler": _PWS_HANDLER,
                "X-Pinterest-Source-Url": SOURCE_URL,
                "X-Pinterest-AppState": "active",
                "Referer": f"{self._base}{SOURCE_URL}",
            }
        )

    @property
    def csrf(self) -> str:
        found = sorted(
            (c for c in self._session.cookies if c.name == "csrftoken"),
            key=lambda c: len(c.domain),
        )
        return next(
            (c.value or "" for c in reversed(found) if _sent_to(c, self._host)), ""
        )

    def cookies_txt(self) -> str:
        return _as_txt(iter(self._session.cookies))

    @property
    def signed_in(self) -> bool:
        return any(c.name == LOGIN_COOKIE for c in self._session.cookies)

    def me(self) -> PinterestAccount:
        data = self._get("/v3/users/me/", {"fields": "user.id,user.username"})
        return PinterestAccount.model_validate(data)

    def boards(self) -> list[PinterestBoard]:
        data = self._get(
            "/v3/users/me/boards/",
            {"fields": "board.id,board.name,board.privacy", "page_size": 100},
        )
        return [PinterestBoard.model_validate(b) for b in data or []]

    def board(self, name: str) -> PinterestBoard:
        found = next(
            (b for b in self.boards() if b.name.casefold() == name.casefold()), None
        )
        if found is not None:
            return found
        data = self._resource(
            "BoardResource/create",
            {
                "name": name,
                "description": "",
                "privacy": "public",
                "layout": "default",
                "category": "other",
            },
            "board create",
            headers={
                "X-Pinterest-PWS-Handler": "www/[username].js",
                "X-Pinterest-Source-Url": "/",
            },
        )
        made = PinterestBoard.model_validate(data)
        logger.info("pinterest: made board %r (%s)", made.name, made.id)
        return made

    def upload(self, path: Path, *, video_ms: int | None = None) -> str:
        slot = self._register(video_ms)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        fields = slot.upload_parameters
        tries = settings.pinterest.upload_tries
        for attempt in range(1, tries + 1):
            try:
                with path.open("rb") as f:
                    answer = self._session.post(
                        slot.upload_url,
                        data=fields,
                        files={"file": (path.name, f, mime)},
                        timeout=settings.pinterest.upload_timeout_s,
                    )
                break
            except requests.RequestException as exc:
                msg = f"Pinterest upload failed: {type(exc).__name__}"
                if attempt == tries:
                    raise PinterestError(msg) from exc
                logger.warning("%s, try %s of %s", msg, attempt, tries, exc_info=True)
                time.sleep(2 * attempt)
        if answer.status_code >= HTTPStatus.BAD_REQUEST:
            msg = f"Pinterest upload answered {answer.status_code}"
            raise PinterestError(msg)
        return slot.upload_id

    def processed(self, upload_id: str) -> str:
        s = settings.pinterest
        deadline = time.monotonic() + s.upload_wait_s
        while True:
            state = self._state(upload_id)
            if state.done:
                return state.signature or ""
            if state.failed:
                msg = f"Pinterest could not process the file ({state.failure_code})"
                raise PinterestMediaError(msg)
            if time.monotonic() > deadline:
                msg = (
                    f"Pinterest still processing the file after {s.upload_wait_s:.0f} s"
                )
                raise PinterestError(msg)
            time.sleep(s.upload_poll_s)

    def create_pin(self, draft: PinDraft) -> CreatedPin:
        tracking_id = str(random.randrange(10**18, 10**19))  # noqa: S311
        body = PinCreate.of(draft, tracking_id).model_dump()
        data = self._create("/v3/storypins/", body)
        found = data.get("id") if isinstance(data, dict) else None
        if not found:
            msg = "Pinterest made no pin (no id in its answer)"
            raise PinterestError(msg)
        return CreatedPin(id=str(found))

    def _register(self, video_ms: int | None) -> UploadSlot:
        key = str(uuid.uuid4())
        info: dict[str, Any] = {"id": key, "media_type": "image-story-pin"}
        if video_ms is not None:
            info["media_type"] = "video-story-pin"
            info["upload_aux_data"] = {
                "clips": [
                    {
                        "durationMs": video_ms,
                        "isFromImage": False,
                        "startTimestampMs": -1,
                    }
                ]
            }
        data = self._create(
            "/v3/media/uploads/register/batch/",
            {"media_info_list": json.dumps([info])},
        )
        try:
            return UploadSlot.model_validate(data[key])
        except (KeyError, TypeError, ValidationError) as exc:
            msg = "Pinterest gave no upload slot"
            raise PinterestError(msg) from exc

    def _state(self, upload_id: str) -> UploadState:
        data = self._get("/v3/media/uploads/", {"upload_ids": [upload_id]})
        try:
            return UploadState.model_validate(data[upload_id])
        except (KeyError, TypeError, ValidationError) as exc:
            msg = "Pinterest did not say how the upload is going"
            raise PinterestError(msg) from exc

    def _get(self, path: str, data: dict[str, Any]) -> Any:
        return self._api("get", path, data)

    def _create(self, path: str, data: dict[str, Any]) -> Any:
        return self._api("create", path, data)

    def _api(self, action: str, path: str, data: dict[str, Any]) -> Any:
        options = {"url": path, "data": data}
        return self._resource(f"ApiResource/{action}", options, path)

    def _resource(
        self,
        resource: str,
        options: dict[str, Any],
        what: str,
        headers: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self._base}/resource/{resource}/"
        body = {
            "source_url": SOURCE_URL,
            "data": json.dumps({"options": options, "context": {}}),
        }
        timeout = settings.pinterest.timeout_s
        try:
            if resource.endswith("/get"):
                answer = self._session.get(
                    url,
                    params={**body, "_": str(int(time.time() * 1000))},
                    headers=headers,
                    timeout=timeout,
                )
            else:
                answer = self._session.post(
                    url,
                    data=body,
                    headers={"X-CSRFToken": self.csrf, **(headers or {})},
                    timeout=timeout,
                )
        except requests.RequestException as exc:
            msg = f"Pinterest {what} failed: {type(exc).__name__}"
            raise PinterestError(msg) from exc
        return _answer(answer, what)


def _snippet(answer: requests.Response) -> str:
    return " ".join(answer.text[:240].split())


def _answer(answer: requests.Response, what: str) -> Any:
    status = answer.status_code
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        wait = answer.headers.get("Retry-After", "")
        logger.warning("pinterest %s rate limited (Retry-After %r)", what, wait)
        raise PinterestRateLimitedError(float(wait) if wait.isdigit() else None)
    if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
        text = _snippet(answer)
        if "Invalid Resource Request" in text:
            logger.error(
                "pinterest %s refused the request (%s): %s", what, status, text
            )
        else:
            logger.warning(
                "pinterest %s answered %s (logged out): %s", what, status, text
            )
        raise PinterestLoggedOutError
    try:
        envelope = Envelope.model_validate_json(answer.content)
    except ValidationError:
        logger.warning(
            "pinterest %s answered %s %s, not its JSON: %s",
            what,
            status,
            answer.headers.get("Content-Type", "?"),
            _snippet(answer),
        )
        msg = f"Pinterest {what} answered {status}, not JSON"
        raise PinterestError(msg) from None
    reply = envelope.resource_response
    if reply.status == "success":
        return reply.data
    error = reply.error or ResourceError()
    logger.warning(
        "pinterest %s refused: %s (code %s, http %s)",
        what,
        error.message or reply.message,
        error.code,
        error.http_status or status,
    )
    if (error.http_status or status) == HTTPStatus.TOO_MANY_REQUESTS:
        raise PinterestRateLimitedError(None)
    msg = f"Pinterest refused {what}: {error.message or reply.message or status}"
    raise PinterestError(msg, permanent=status < HTTPStatus.INTERNAL_SERVER_ERROR)
