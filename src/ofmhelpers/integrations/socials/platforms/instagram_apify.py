"""Instagram through Apify, the first source: no proxies, no login, no ban.

Per batch, every key in `APIFY_API_KEYS` most credit first; a key takes as
many accounts as its credit covers (`apify_usd_per_item` x (profile +
`apify_est_reels`)). Per key two runs: the profile scraper (followers,
avatar, private) for all of them, then the reel scraper for the public ones,
only reels of the last `OFM_ACCOUNTS_APIFY_DAYS` (play counts) -- older ones
keep the views the last scan saw.

What a key did not read goes to the next key, then to the next source:
- profile run failed or out of credit: its accounts (nothing was read);
- reel run failed, out of credit or cut short: only its public accounts
  (half a reel read would save wrong views); the private ones and the
  profiles already paid for are kept;
- an account the profile run, having succeeded, did not return (not found):
  straight to the next source, never paid for again on another key.

One batch at a time across every worker (`_one_at_a_time`): parallel jobs
sizing their share from the same credit figure would spend it several times
over. A handle two users track is read once."""

import time
from contextlib import contextmanager, suppress
from typing import Any
lazy from collections.abc import Callable, Generator

import requests
from pydantic import TypeAdapter, ValidationError
from redis.exceptions import LockError, RedisError

from ofmhelpers.cache.redis import get_redis
from ofmhelpers.config import settings
from ofmhelpers.integrations.apify import (
    ApifyClient,
    ApifyError,
    RunStatus,
    by_credit,
)
from ofmhelpers.integrations.apify.instagram import ApifyProfile, ApifyReel
from ofmhelpers.integrations.instagram import ProfileHandle
from ofmhelpers.integrations.instagram.models import MediaUrl
from ofmhelpers.integrations.socials.models import (
    AccountProfile,
    AccountStats,
    PostStat,
)
from ofmhelpers.integrations.socials.sources import (
    AccountSource,
    Answers,
    ScanTarget,
)
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

_MEDIA_URL: TypeAdapter[str] = TypeAdapter(MediaUrl)
DAY_S = 86400
_LOCK = "lock:accounts-apify"


def profile_input(handles: list[str]) -> dict[str, Any]:
    return {"usernames": handles, "includeAboutSection": False}


def reel_input(handles: list[str], limit: int, days: int) -> dict[str, Any]:
    """Each account's reels of the last `days`, at most `limit` (always set:
    without it the actor reads the whole profile), every paid add-on off."""
    return {
        "username": handles,
        "resultsLimit": limit,
        "onlyPostsNewerThan": f"{days} days",
        "skipPinnedPosts": False,
        "includeSharesCount": False,
        "includeTranscript": False,
        "includeDownloadedVideo": False,
    }


def _parsed[T](
    items: list[dict[str, Any]], model: Callable[[dict[str, Any]], T], what: str
) -> list[T]:
    """The items `model` reads; error items and unreadable ones logged."""
    found: list[T] = []
    for item in items:
        if "error" in item:
            logger.info("apify %s: %s %s", what, item.get("error"), item.get("url", ""))
            continue
        try:
            found.append(model(item))
        except ValidationError:
            logger.warning("apify %s: unreadable item", what, exc_info=True)
    return found


def _handle_of(profile: ApifyProfile, asked: set[str]) -> str | None:
    """Which asked handle `profile` answers: its username, else (renamed
    since) the profile URL it was asked by."""
    name = profile.username.lower()
    if name in asked:
        return name
    try:
        old = ProfileHandle(username=profile.input_url).username
    except ValueError:
        return None
    return old if old in asked else None


def _stats(profile: ApifyProfile, reels: list[ApifyReel], now: float) -> AccountStats:
    days = settings.accounts.apify_days
    cap = settings.instagram.track_max_reels
    unique = {r.code: r for r in reels if r.posted_at is not None}
    dated = sorted(
        unique.values(),
        key=lambda r: r.posted_at.timestamp() if r.posted_at else 0,
        reverse=True,
    )
    capped = len(dated) > cap
    return AccountStats(
        profile=AccountProfile(
            handle=profile.username.lower(),
            account_id=profile.id,
            display_name=profile.full_name,
            followers=profile.followers,
            following=profile.following,
            is_private=profile.is_private,
            is_verified=profile.is_verified,
            avatar_url=_MEDIA_URL.validate_python(
                profile.avatar_url or profile.small_avatar_url
            ),
        ),
        posts=[
            PostStat(
                id=r.code,
                url=f"https://www.instagram.com/reel/{r.code}/",
                views=r.plays,
                posted_at=r.posted_at.timestamp(),
                thumbnail_url=_MEDIA_URL.validate_python(r.thumbnail_url),
            )
            for r in dated[:cap]
            if r.posted_at is not None
        ],
        # Only the last `days` were asked for: every post from then on,
        # unless the cap cut in first (then from its oldest post).
        complete=False,
        since=None if capped or profile.is_private else now - days * DAY_S,
        scanned_at=now,
    )


@contextmanager
def _one_at_a_time(keys: int, wait_s: float | None = None) -> Generator[bool]:
    """Held while a batch spends: True. Not had within `wait_s` (default:
    the longest a batch may take, every key's two runs): False, the batch
    goes to the next source. Redis down: logged, True (spending unlocked
    beats not reading)."""
    longest = keys * 2 * (settings.accounts.apify_run_timeout_s + 60) + 60
    wait = longest if wait_s is None else wait_s
    lock = get_redis().lock(_LOCK, timeout=longest, blocking_timeout=wait)
    try:
        held = bool(lock.acquire())
    except RedisError:
        logger.warning("apify: batch lock unavailable, going ahead", exc_info=True)
        yield True
        return
    if not held:
        logger.warning(
            "apify: another batch held the lock %s s; this one goes on", wait
        )
        yield False
        return
    try:
        yield True
    finally:
        with suppress(RedisError, LockError):
            lock.release()


class ApifyInstagramSource(AccountSource):
    key = "apify"

    def scan(self, targets: list[ScanTarget]) -> Answers:
        answers = Answers()
        keys = settings.viral.api_keys
        if not targets or not keys:
            return answers
        owners: dict[str, list[str]] = {}
        for target in targets:
            owners.setdefault(target.handle, []).append(target.key)
        with _one_at_a_time(len(keys)) as held:
            read = self._through_keys(keys, list(owners)) if held else {}
        for handle, stats in read.items():
            for key in owners[handle]:
                answers.stats[key] = stats
        return answers

    def _through_keys(
        self, keys: list[str], handles: list[str]
    ) -> dict[str, AccountStats]:
        """`handles` through the keys, most credit first; by handle."""
        s = settings.accounts
        each = s.apify_usd_per_item * (1 + s.apify_est_reels)
        read: dict[str, AccountStats] = {}
        left = list(handles)
        for client, credit in by_credit(keys):
            if not left:
                break
            room = int((credit - 2 * s.apify_usd_per_run) // each)
            if room < 1:
                logger.info(
                    "apify key %s: $%.2f left, too little", client.key_hint, credit
                )
                continue
            group, left = left[:room], left[room:]
            try:
                got, gave_up = self._read(client, group)
            except (ApifyError, requests.RequestException) as exc:
                logger.warning(
                    "apify key %s: profile run failed on %d accounts (%s); next key",
                    client.key_hint,
                    len(group),
                    exc,
                )
                got, gave_up = {}, set()
            read.update(got)
            # Not read and not given up on: the next key tries them first.
            left = [h for h in group if h not in got and h not in gave_up] + left
        return read

    def _read(
        self, client: ApifyClient, handles: list[str]
    ) -> tuple[dict[str, AccountStats], set[str]]:
        """The accounts `client` read, and the ones the actor answered it
        cannot read (not found: no other key will do better). Raises only
        when the profile run itself fails (nothing read, nothing to keep)."""
        s = settings.accounts
        now = time.time()
        asked = set(handles)
        run = client.run(
            s.apify_profile_actor, profile_input(handles), s.apify_run_timeout_s
        )
        profiles: dict[str, ApifyProfile] = {}
        for profile in _parsed(
            client.items(run), ApifyProfile.model_validate, "profile"
        ):
            handle = _handle_of(profile, asked)
            if handle is not None:
                profiles[handle] = profile
        gave_up = asked - set(profiles) if run.status == RunStatus.SUCCEEDED else set()
        public = [h for h, p in profiles.items() if not p.is_private]
        reels, cost = self._reels(client, {h: profiles[h] for h in public})
        read = {
            h: _stats(p, (reels or {}).get(h, []), now)
            for h, p in profiles.items()
            if p.is_private or reels is not None
        }
        logger.info(
            "apify key %s: %d of %d accounts read, %d reels, $%.4f",
            client.key_hint,
            len(read),
            len(handles),
            sum(len(r) for r in (reels or {}).values()),
            run.cost_usd + cost,
        )
        return read, gave_up

    @staticmethod
    def _reels(
        client: ApifyClient, public: dict[str, ApifyProfile]
    ) -> tuple[dict[str, list[ApifyReel]] | None, float]:
        """The public accounts' reels of the last days by asked handle, and
        what the run cost; None when the run did not finish whole (failed,
        out of credit, cut short): their accounts go on, unread."""
        if not public:
            return {}, 0.0
        s = settings.accounts
        # Asked by the current username: a renamed account's old handle may
        # be nobody's, or someone else's, by now.
        handle_of = {p.username.lower(): h for h, p in public.items()}
        try:
            run = client.run(
                s.apify_reel_actor,
                reel_input(
                    list(handle_of),
                    settings.instagram.track_max_reels + 1,
                    s.apify_days,
                ),
                s.apify_run_timeout_s,
            )
            if run.status != RunStatus.SUCCEEDED:
                logger.warning(
                    "apify reel run %s %s: %s; its %d accounts go on",
                    run.id,
                    run.status,
                    run.status_message or "no reason given",
                    len(public),
                )
                return None, run.cost_usd
            items = client.items(run)
        except (ApifyError, requests.RequestException) as exc:
            logger.warning(
                "apify key %s: reel run failed (%s); its %d accounts go on",
                client.key_hint,
                exc,
                len(public),
            )
            return None, 0.0
        reels: dict[str, list[ApifyReel]] = {h: [] for h in public}
        for reel in _parsed(items, ApifyReel.model_validate, "reel"):
            handle = handle_of.get(reel.owner.lower())
            if handle is not None:
                reels[handle].append(reel)
        return reels, run.cost_usd
