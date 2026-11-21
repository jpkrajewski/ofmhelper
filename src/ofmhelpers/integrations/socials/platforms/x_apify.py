import time
lazy from collections.abc import Callable

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.integrations.apify import (
    ApifyClient,
    ApifyCreditError,
    RunStatus,
    by_credit,
)
from ofmhelpers.integrations.apify.x import (
    MOCK_TWEET,
    ApifyTweet,
    ApifyTweetAuthor,
    TweetSearchInput,
)
from ofmhelpers.integrations.socials.models import (
    AccountProfile,
    AccountStats,
    PostStat,
)
from ofmhelpers.integrations.socials.platforms.instagram_apify import (
    DAY_S,
    one_apify_batch,
    parsed_items,
)
from ofmhelpers.integrations.socials.sources import (
    AccountSource,
    Answers,
    Landed,
    ScanTarget,
)
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

_SMALL_AVATAR = "_normal."
_BIG_AVATAR = "_400x400."


def _not_saved(_key: str, _stats: AccountStats) -> None:
    return None


def tweet_input(handle: str, since: float) -> TweetSearchInput:
    return TweetSearchInput(
        author=handle,
        since_time=str(int(since)),
        max_items=settings.accounts.apify_x_max_items,
    )


class _Window(BaseModel, frozen=True):
    since: float
    now: float
    capped: bool


def _stats(
    handle: str, author: ApifyTweetAuthor, tweets: list[ApifyTweet], window: _Window
) -> AccountStats:
    unique = {t.id: t for t in tweets}
    return AccountStats(
        profile=AccountProfile(
            handle=handle,
            account_id=author.id,
            display_name=author.name,
            followers=author.followers,
            following=author.following,
            is_private=bool(author.protected),
            is_verified=author.is_blue_verified,
            avatar_url=author.profile_picture.replace(_SMALL_AVATAR, _BIG_AVATAR),
        ),
        posts=[
            PostStat(
                id=t.id,
                url=f"https://x.com/{handle}/status/{t.id}",
                views=t.views or 0,
                posted_at=t.created_at.timestamp(),
                thumbnail_url=t.thumbnail_url,
            )
            for t in unique.values()
            if t.created_at is not None
        ],
        complete=False,
        since=None if window.capped else window.since,
        scanned_at=window.now,
    )


class ApifyXSource(AccountSource):
    key = "apify"

    def scan(self, targets: list[ScanTarget]) -> Answers:
        return self.scan_saving(targets, _not_saved)

    def scan_saving(self, targets: list[ScanTarget], landed: Landed) -> Answers:
        answers = Answers()
        keys = settings.viral.api_keys
        if not targets or not keys:
            return answers
        owners: dict[str, list[str]] = {}
        for target in targets:
            owners.setdefault(target.handle, []).append(target.key)

        def keep(handle: str, stats: AccountStats) -> None:
            for key in owners[handle]:
                answers.stats[key] = stats
                landed(key, stats)

        with one_apify_batch(len(owners)) as held:
            if held:
                self._through_keys(keys, list(owners), keep)
        return answers

    def _through_keys(
        self,
        keys: list[str],
        handles: list[str],
        keep: Callable[[str, AccountStats], None],
    ) -> None:
        per_run = settings.accounts.apify_x_usd_per_run
        left = list(handles)
        for funded in by_credit(keys):
            room = int(funded.credit_usd // per_run) - 1
            while left and room > 0:
                handle = left.pop(0)
                room -= 1
                try:
                    found = self._read(funded.client, handle)
                except ApifyCreditError:
                    logger.warning(
                        "apify key %s: out of credit at @%s; next key",
                        funded.client.key_hint,
                        handle,
                        exc_info=True,
                    )
                    left.insert(0, handle)
                    break
                except Exception:
                    logger.warning(
                        "apify x run of @%s failed; it goes on", handle, exc_info=True
                    )
                    continue
                if found is not None:
                    keep(handle, found)
            if not left:
                break

    @staticmethod
    def _read(client: ApifyClient, handle: str) -> AccountStats | None:
        s = settings.accounts
        now = time.time()
        since = now - s.apify_days * DAY_S
        run = client.run(
            s.apify_x_actor, tweet_input(handle, since), s.apify_run_timeout_s
        )
        if run.status not in (RunStatus.SUCCEEDED, RunStatus.TIMED_OUT):
            logger.warning(
                "apify x run %s of @%s %s ($%.4f): %s; it goes on",
                run.id,
                handle,
                run.status,
                run.cost_usd,
                run.status_message or "no reason given",
            )
            return None
        items = client.items(run)
        rows = sum(1 for i in items if (i.model_extra or {}).get("type") != MOCK_TWEET)
        tweets = [
            t
            for t in parsed_items(items, ApifyTweet, "tweet")
            if t.own_post and t.author and t.author.user_name.lower() == handle
        ]
        logger.info(
            "apify key %s: @%s %d posts of %d rows, run %s %s, $%.4f",
            client.key_hint,
            handle,
            len(tweets),
            rows,
            run.id,
            run.status,
            run.cost_usd,
        )
        newest = max(tweets, key=lambda t: t.posted_at, default=None)
        if newest is None or newest.author is None or not newest.author.id:
            return None
        capped = run.status == RunStatus.TIMED_OUT or rows >= s.apify_x_max_items
        window = _Window(since=since, now=now, capped=capped)
        return _stats(handle, newest.author, tweets, window)
