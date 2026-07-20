from dataclasses import dataclass
import enum
from typing import Callable


class Scrapers(enum.StrEnum):
    INSTAGRAM_PROFILES = "INSTAGRAM_PROFILES"
    TIKTOK_PROFILES = "TIKTOK_PROFILES"


@dataclass(frozen=True)
class ContentRankingWeights:
    views: float
    like_rate: float
    comment_rate: float
    velocity: float


@dataclass(frozen=True)
class ScraperConfig:
    views_threshold_default: int
    views_threshold_today: int
    content_ranking_weights: ContentRankingWeights
    actor_id: str
    prepare_raw_input_func: Callable[[list[str], int, int], dict]


RESULTS_PER_PROFILE = 3
RESULTS_DAYS_BACK = 3


def prepare_raw_input_instagram_reel_scraper(profiles, results_per_page, days_back):
    # https://console.apify.com/actors/xMc5Ga1oCONPmWJIa/input
    return {
        "username": profiles,
        "resultsLimit": results_per_page,
        "onlyPostsNewerThan": f"{days_back} days",
        "includeDownloadedVideo": False,
        "includeSharesCount": False,
        "includeTranscript": False,
        "skipPinnedPosts": True,
        "skipTrialReels": True,
    }


def prepare_raw_input_tiktok_reel_scraper(profiles, results_per_page, days_back):
    # https://console.apify.com/actors/GdWCkxBtKWOsKjdch/runs/5aAtDRrPzOsRWzgsJ#input
    return {
        "profiles": profiles,
        "profileScrapeSections": ["videos"],
        "profileSorting": "latest",
        "resultsPerPage": results_per_page,
        "oldestPostDateUnified": f"{days_back} days",
        "excludePinnedPosts": True,
        "commentsPerPost": 0,
        "topLevelCommentsPerPost": 0,
        "maxRepliesPerComment": 0,
        "maxFollowersPerProfile": 0,
        "maxFollowingPerProfile": 0,
        "scrapeRelatedVideos": False,
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadAvatars": False,
        "shouldDownloadMusicCovers": False,
        "shouldDownloadSlideshowImages": False,
        "proxyCountryCode": "None",
    }


SCRAPRES_REGISTRY = {
    Scrapers.INSTAGRAM_PROFILES: ScraperConfig(
        views_threshold_default=4,
        views_threshold_today=1,
        content_ranking_weights=ContentRankingWeights(
            views=0.50,
            like_rate=0.20,
            comment_rate=0.20,
            velocity=0.10,
        ),
        actor_id="apify/instagram-reel-scraper",
        prepare_raw_input_func=prepare_raw_input_instagram_reel_scraper,
    ),
    Scrapers.TIKTOK_PROFILES: ScraperConfig(
        views_threshold_default=4,
        views_threshold_today=1,
        content_ranking_weights=ContentRankingWeights(
            views=0.50,
            like_rate=0.20,
            comment_rate=0.20,
            velocity=0.10,
        ),
        actor_id="clockworks/tiktok-scraper",
        prepare_raw_input_func=prepare_raw_input_tiktok_reel_scraper,
    ),
}
