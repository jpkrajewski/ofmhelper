import csv
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.scraping.apify import (
    get_client_with_most_credits,
    run_actor,
)

logger = get_logger(__name__)

ACTOR_ID = "sentry/onlyfans-similar-models"

RAW_INPUT = {
    "seeds": [
        "gay",
        "muscle",
        "jock",
        "twink",
        "bear",
    ],
    "seedKeywords": [
        "gay",
        "male",
        "lgbt",
        "muscle",
        "jock",
        "twink",
        "bear",
    ],
    "additionalKeywords": "gay male",
    "similarityMode": "same-niche",
    "searchMode": "new",
    "maxProfiles": 500,
    "maxQueriesPerSeed": 3,
    "minSimilarityScore": 45,
    "maxLikes": 50000,
    "scrapeOtherSocials": True,
}

OUTPUT_PATH = "onlyfans_gay_models.csv"

FIELDNAMES = [
    "username",
    "name",
    "profileUrl",
    "avatar",
    "bio",
    "price",
    "likes",
    "instagram",
    "twitter",
    "tiktok",
    "fansly",
]


def write_csv(rows, output_path):
    path = Path(output_path)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=FIELDNAMES,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def main():
    client = get_client_with_most_credits(settings.apify.api_keys)

    profiles = run_actor(
        client=client,
        actor_id=ACTOR_ID,
        raw_input=RAW_INPUT,
    )

    write_csv(profiles, OUTPUT_PATH)

    logger.info("Found %d profiles", len(profiles))


if __name__ == "__main__":
    main()
