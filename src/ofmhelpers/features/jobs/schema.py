# Copyright (C) 2026 jpkrajewski

import re
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, computed_field

FALLBACK = "Failed"
_WHO = re.compile(r"^([A-Za-z][\w.\- ]{1,23}): (.+)$", re.DOTALL)
_SAID_MAX = 240


class ErrorReason(BaseModel):
    label: str
    pattern: str


REASONS = (
    ErrorReason(
        label="Could not download",
        pattern=r"^(could not download|no video at this link|the video could not"
        r"|the video cannot)",
    ),
    ErrorReason(label="Needs login", pattern=r"^needs login"),
    ErrorReason(
        label="Post deleted or profile banned",
        pattern=r"empty media response|video unavailable"
        r"|content isn.t available|has been (removed|deleted|terminated)",
    ),
    ErrorReason(label="Needs login", pattern=r"redirect(ed)? to (the )?login page"),
    ErrorReason(label="Too long", pattern=r"^too long"),
    ErrorReason(label="Stopped", pattern=r"^stopped\b"),
    ErrorReason(label="Export failed", pattern=r"^export failed"),
    ErrorReason(label="Profile not found", pattern=r"^profile not found"),
    ErrorReason(label="Age-restricted profile", pattern=r"^age-restricted profile"),
    ErrorReason(label="No reels", pattern=r"^no reels"),
    ErrorReason(label="No Apify credit", pattern=r"^no apify credit"),
    ErrorReason(label="Chinese server is slow", pattern=r"chinese server is slow"),
    ErrorReason(label="Still generating", pattern=r"still generating"),
    ErrorReason(
        label="No credits",
        pattern=r"credits? insufficient|insufficient (credit|balance)"
        r"|credit balance is too low|not.enough.wallet|insufficient.funds|\b402\b",
    ),
    ErrorReason(
        label="Wrong API key",
        pattern=r"\b401\b|unauthori[sz]ed|invalid (api )?key|api key",
    ),
    ErrorReason(
        label="Flagged content",
        pattern=r"nsfw|sensitive|flagged|content polic|moderat|prohibited"
        r"|content verification"
        r"|\[datainspectionfailed\]|\[ipinfringementsuspect\]",
    ),
    ErrorReason(
        label="Rate limited",
        pattern=r"\b429\b|rate.?limit|too many requests|queue.maxed|instance.maxed"
        r"|concurrency limit",
    ),
    ErrorReason(label="Timed out", pattern=r"time ?out|timed out"),
    ErrorReason(
        label="Input out of range",
        pattern=r"out of range|must be between|\b422\b",
    ),
    ErrorReason(
        label="Bad input file",
        pattern=r"cannot identify|not "
        r"supported|unsupported|invalid (input|image|video)",
    ),
    ErrorReason(
        label="Provider error",
        pattern=r"internal (server )?error|try again later|\b50[0-4]\b",
    ),
)


class _CompiledReason(BaseModel, frozen=True, arbitrary_types_allowed=True):
    pattern: re.Pattern[str]
    label: str


def _compile(reasons: tuple[ErrorReason, ...]) -> list[_CompiledReason]:
    return [
        _CompiledReason(pattern=re.compile(r.pattern, re.IGNORECASE), label=r.label)
        for r in reasons
    ]


@lru_cache(maxsize=1)
def _compiled() -> list[_CompiledReason]:
    return _compile(REASONS)


def error_reason(error: str | None) -> str:
    if error:
        for reason in _compiled():
            if reason.pattern.search(error):
                return reason.label
    return FALLBACK


class _Refusal(BaseModel):
    who: str
    reason: str
    said: str


def _refusal(error: str) -> _Refusal:
    reason = error_reason(error)
    text = " ".join(error.split())
    found = _WHO.match(text)
    who, said = (found.group(1), found.group(2)) if found else ("", text)
    if who.lower() == reason.lower():
        who, said = "", text
    return _Refusal(who=who, reason=reason, said=said)


def error_line(error: str | None) -> str:
    if not error:
        return FALLBACK
    refusal = _refusal(error)
    line = _reasoned(refusal.reason, refusal.said)
    return _capped(f"{refusal.who}: {line}" if refusal.who else line)


_KEY_REASONS = frozenset({"No credits", "Wrong API key"})


class ErrorText(BaseModel):
    head: str
    said: str = ""
    about_keys: bool = False


def error_text(error: str | None) -> ErrorText:
    if not error:
        return ErrorText(head=FALLBACK)
    refusal = _refusal(error)
    said = refusal.said
    if said.lower().startswith(refusal.reason.lower()):
        said = said[len(refusal.reason) :].lstrip(" ,:;.-")
    head = f"{refusal.who}: {refusal.reason}" if refusal.who else refusal.reason
    return ErrorText(
        head=head,
        said=_capped(said),
        about_keys=refusal.reason in _KEY_REASONS,
    )


def _reasoned(reason: str, said: str) -> str:
    if said.lower().startswith(reason.lower()):
        return reason + said[len(reason) :]
    if reason.lower() in said.lower():
        return said
    return f"{reason}, {said}"


def _capped(text: str) -> str:
    return text if len(text) <= _SAID_MAX else f"{text[: _SAID_MAX - 1].rstrip()}…"


DOWNLOAD_REASONS = (
    ErrorReason(
        label="Private account: only its followers can see it",
        pattern=r"only available for registered users who follow"
        r"|this account is private|private account",
    ),
    ErrorReason(
        label="No video in this post (photos only)",
        pattern=r"there is no video in this post",
    ),
    ErrorReason(
        label="Instagram gave no video (private, age-restricted or blocked)",
        pattern=r"no video formats found",
    ),
    ErrorReason(
        label="Post deleted or profile banned",
        pattern=r"empty media response|video "
        r"unavailable|this video is (private|unavailable)"
        r"|content isn.t available|has been (removed|deleted|terminated)",
    ),
    ErrorReason(
        label="Not found: the post is gone or the link is wrong",
        pattern=r"\b404\b|not found",
    ),
    ErrorReason(
        label="Not a link we can download",
        pattern=r"unsupported url|is not a valid url|no connection adapters",
    ),
    ErrorReason(
        label="Instagram wants a login: upload fresh cookies on Cookies",
        pattern=r"needs a login|redirect(ed)? to (the )?login page"
        r"|redirect to home page|\b401\b",
    ),
    ErrorReason(
        label="YouTube wants a login (bot check): upload YouTube cookies on Cookies",
        pattern=r"sign in to confirm you.re not a bot",
    ),
    ErrorReason(
        label="The site refused us (403)",
        pattern=r"\b403\b|forbidden",
    ),
    ErrorReason(
        label="Instagram blocked the download for now: try again later",
        pattern=r"\b400\b|bad request|unable to extract|video info extraction failed",
    ),
    ErrorReason(
        label="Connection failed (proxy or site down): try again",
        pattern=r"proxyerror|connect tunnel failed|\b50[0-4]\b|bad gateway"
        r"|cipher list|timed? ?out",
    ),
)
_DOWNLOADER_PREFIX = re.compile(
    r"^(?:\w+: )?(?:ERROR: )?(?:\[[\w:.-]+\](?:\[error\])? )?(?:[\w-]+: )?",
    re.IGNORECASE,
)
_ADVICE = re.compile(r";\s*please report|\.\s*use --|\s*\(caused by", re.IGNORECASE)
_DOWNLOAD_SAID_MAX = 120


@lru_cache(maxsize=1)
def _download_compiled() -> list[_CompiledReason]:
    return _compile(DOWNLOAD_REASONS)


def download_reason(error: str | None) -> str:
    if not error:
        return "Could not download"
    for reason in _download_compiled():
        if reason.pattern.search(error):
            return reason.label
    first = " ".join(error.split(" | ")[0].split())
    said = _ADVICE.split(_DOWNLOADER_PREFIX.sub("", first), maxsplit=1)[0].strip()
    if not said:
        return "Could not download"
    capped = said[: _DOWNLOAD_SAID_MAX - 1].rstrip() + "…"
    return said if len(said) <= _DOWNLOAD_SAID_MAX else capped


class FailedSource(BaseModel):
    source: str
    error: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def link(self) -> str:
        parts = urlsplit(self.source)
        if not parts.netloc:
            return self.source
        host = parts.netloc.removeprefix("www.")
        return f"{host}{parts.path.rstrip('/')}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reason(self) -> str:
        return download_reason(self.error)


class JobQueued(BaseModel):
    job_id: str


class JobsQueued(BaseModel):
    job_ids: list[str]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def job_id(self) -> str:
        return self.job_ids[0]


class JobVisibility(BaseModel):
    hidden: bool


class CloneQueued(BaseModel):
    job_ids: list[str]
    html: str
    batch: str | None = None


class InputSetting(BaseModel):
    label: str
    value: Any


class ActionLogEntry(BaseModel):
    task: str
    actor: str | None
    actor_role: str = "user"
    status: str
    params: dict[str, Any]
    status_url: str | None
    created_at_display: str


class CloneSource(BaseModel):
    name: str
    thumb_url: str
