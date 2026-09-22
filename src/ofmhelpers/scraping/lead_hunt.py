"""Creator lead hunt: find small Instagram accounts that promote OnlyFans.

The shape of a run, and why each step exists:

    hashtags -> [hashtag actor] -> candidate usernames (deduped)
             -> [profile actor] -> followers + bio + link fields
             -> follower band   -> only sub-10k accounts survive
             -> of_links/aggregators -> CERTAIN vs LIKELY
             -> leads.xlsx

Instagram cannot be enumerated, so discovery has to start somewhere: a
hashtag is the cheapest public seed that is already filtered to the niche.
Dedup matters more than it looks -- one active account fills a niche tag
with a dozen posts, so 100 posts is nothing like 100 candidates.

Reads only what a logged-out visitor sees, through Apify's public actors.
The follower ceiling is the point of the exercise, not a nicety: an
account already over it has representation.

Everything here is fault-isolated per profile (see scraping/CLAUDE.md): a
bad dataset item, or an aggregator that will not load, costs one row and
never the run.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.scraping.aggregators import resolve_first
from ofmhelpers.scraping.apify import get_client_with_most_credits, run_actor
from ofmhelpers.scraping.lead_exporter import LeadExcelExporter
from ofmhelpers.scraping.models import Confidence, InstagramProfile, Lead
from ofmhelpers.scraping.of_links import (
    find_aggregator_urls,
    find_direct_of_url,
    mentions_onlyfans,
)

if TYPE_CHECKING:
    from apify_client import ApifyClient

logger = get_logger(__name__)


def prepare_hashtag_input(hashtags: list[str], results_limit: int) -> dict:
    # https://console.apify.com/actors/apify~instagram-hashtag-scraper/input
    return {
        "hashtags": [tag.lstrip("#") for tag in hashtags],
        "resultsLimit": results_limit,
        "resultsType": "posts",
    }


def prepare_profile_input(usernames: list[str]) -> dict:
    # https://console.apify.com/actors/apify~instagram-profile-scraper/input
    return {"usernames": usernames}


def discover_usernames(client: ApifyClient, hashtags: list[str]) -> list[str]:
    """Candidate usernames behind the posts on those hashtags, deduped, in
    first-seen order, capped at the per-run profile budget."""
    cfg = settings.lead_hunt
    items = run_actor(
        client=client,
        actor_id=cfg.hashtag_actor_id,
        raw_input=prepare_hashtag_input(hashtags, cfg.posts_per_hashtag),
    )

    # dict, not set: first-seen order is the rough popularity order the
    # hashtag page returned, and that is the order worth spending the
    # profile-actor budget in.
    seen: dict[str, None] = {}
    for item in items:
        username = item.get("ownerUsername") or item.get("username")
        if username:
            seen.setdefault(str(username).lower(), None)

    usernames = list(seen)
    logger.info(
        "hashtag discovery: %d posts -> %d unique usernames",
        len(items),
        len(usernames),
    )
    return usernames[: cfg.max_profiles_per_run]


def fetch_profiles(client: ApifyClient, usernames: list[str]) -> list[InstagramProfile]:
    """Followers, bio and link fields for each username. A dataset item the
    model cannot parse is logged and dropped -- the actor is third-party and
    one odd row must not end the run."""
    if not usernames:
        return []

    items = run_actor(
        client=client,
        actor_id=settings.lead_hunt.profile_actor_id,
        raw_input=prepare_profile_input(usernames),
    )

    profiles: list[InstagramProfile] = []
    for item in items:
        try:
            profile = InstagramProfile.from_apify(item)
        except Exception:
            logger.warning("unparseable profile item skipped", exc_info=True)
            continue
        if profile.is_valid():
            profiles.append(profile)

    logger.info("profile enrich: %d items -> %d usable", len(items), len(profiles))
    return profiles


def in_follower_band(profile: InstagramProfile) -> bool:
    """The sub-10k window from settings. Exclusive at the top: "less than
    10k" is the brief, and an account sitting exactly on the line reads as
    already grown."""
    cfg = settings.lead_hunt
    followers = profile.followers
    return followers is not None and cfg.min_followers <= followers < cfg.max_followers


def qualify(profile: InstagramProfile) -> Lead | None:
    """The profile as a lead, or None if it is not one.

    Ordered by how sure each signal makes us: a real onlyfans.com URL in the
    bio beats one found a hop away through an aggregator, which beats the
    word on its own."""
    if not in_follower_band(profile):
        return None

    text = profile.searchable_text

    direct = find_direct_of_url(text)
    if direct:
        return Lead(
            profile=profile,
            confidence=Confidence.CERTAIN,
            onlyfans_url=direct,
            evidence="onlyfans link in bio",
        )

    aggregators = find_aggregator_urls(text)
    if aggregators:
        if settings.lead_hunt.resolve_aggregators:
            resolved, source = resolve_first(aggregators)
            if resolved:
                return Lead(
                    profile=profile,
                    confidence=Confidence.CERTAIN,
                    onlyfans_url=resolved,
                    evidence=f"via {source}",
                )
        return Lead(
            profile=profile,
            confidence=Confidence.LIKELY,
            evidence=f"aggregator {aggregators[0]}, no onlyfans link found",
        )

    if mentions_onlyfans(text):
        return Lead(
            profile=profile,
            confidence=Confidence.LIKELY,
            evidence="bio mentions OnlyFans, no link",
        )

    return None


def qualify_all(profiles: list[InstagramProfile]) -> list[Lead]:
    """Qualifies every profile, CERTAIN first then biggest audience. One
    profile blowing up costs that row only."""
    leads: list[Lead] = []
    for profile in profiles:
        try:
            lead = qualify(profile)
        except Exception:
            logger.warning(
                "qualify failed username=%s", profile.username, exc_info=True
            )
            continue
        if lead is not None:
            leads.append(lead)
    return sorted(leads, key=Lead.sort_key)


def hunt(hashtags: list[str], api_keys: list[str] | None = None) -> list[Lead]:
    """Whole pipeline: hashtags in, ranked leads out."""
    keys = api_keys if api_keys is not None else settings.lead_hunt.api_keys
    if not keys:
        msg = "No Apify API key -- set OFM_APIFY_API_KEYS or pass --api-key."
        raise RuntimeError(msg)

    client = get_client_with_most_credits(keys)
    usernames = discover_usernames(client, hashtags)
    profiles = fetch_profiles(client, usernames)
    leads = qualify_all(profiles)
    logger.info("hunt done: %d profiles -> %d leads", len(profiles), len(leads))
    return leads


def read_hashtags(args: argparse.Namespace) -> list[str]:
    """--hashtag-file wins over --hashtag; blank lines and `#!`-commented
    lines are skipped, a leading `#` on a real tag is not (it is how people
    write hashtags)."""
    if args.hashtag_file:
        lines = Path(args.hashtag_file).read_text(encoding="utf-8").splitlines()
        return [
            line.strip()
            for line in lines
            if line.strip() and not line.lstrip().startswith("#!")
        ]
    return list(args.hashtag)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m ofmhelpers.scraping.lead_hunt",
        description="Find sub-10k Instagram accounts promoting OnlyFans.",
    )
    parser.add_argument(
        "--hashtag",
        action="append",
        default=[],
        help="repeatable, e.g. --hashtag ofmodel --hashtag ofpromo",
    )
    parser.add_argument("--hashtag-file", help="one hashtag per line")
    parser.add_argument(
        "--api-key",
        action="append",
        default=[],
        help="Apify token, repeatable; defaults to OFM_APIFY_API_KEYS",
    )
    parser.add_argument("--out", default="leads.xlsx", help="output .xlsx path")
    parser.add_argument(
        "--max-followers", type=int, help="override OFM_LEADS_MAX_FOLLOWERS"
    )
    parser.add_argument(
        "--no-resolve",
        action="store_true",
        help="skip the link-aggregator page fetch (faster, fewer CERTAIN rows)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Operator CLI: it exists to print a report, so print() rather than the
    logger -- same rule as web/db/backfill_remote_urls.py."""
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    hashtags = read_hashtags(args)
    if not hashtags:
        print("Nothing to hunt: pass --hashtag or --hashtag-file.")
        return 2

    # CLI flags are the short-lived override of the env-driven defaults, so
    # they are applied where the settings group reads them from. Each settings
    # group constructs fresh on access (see config/__init__.py), so this is
    # picked up by the very next read.
    if args.max_followers is not None:
        os.environ["OFM_LEADS_MAX_FOLLOWERS"] = str(args.max_followers)
    if args.no_resolve:
        os.environ["OFM_LEADS_RESOLVE_AGGREGATORS"] = "false"

    leads = hunt(hashtags, api_keys=args.api_key or None)
    LeadExcelExporter().export(leads, args.out)

    certain = sum(1 for lead in leads if lead.confidence is Confidence.CERTAIN)
    print(f"hashtags: {', '.join(hashtags)}")
    print(f"leads:    {len(leads)} ({certain} certain, {len(leads) - certain} likely)")
    print(f"written:  {args.out}")
    return 0


if __name__ == "__main__":
    from ofmhelpers.log import configure_logging

    configure_logging()
    sys.exit(main())
