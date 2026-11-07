"""The Apify source's reading of one key's runs, against a stub client."""

from datetime import UTC, datetime, timedelta
from typing import Any

from ofmhelpers.integrations.apify import ActorRun, RunStatus
from ofmhelpers.integrations.socials.platforms.instagram_apify import (
    ApifyInstagramSource,
)


class _Client:
    """Answers the profile run and the reel run with the given items."""

    key_hint = "...stub"

    def __init__(self, profiles: list[dict], reels: list[dict]) -> None:
        self.inputs: list[tuple[str, dict[str, Any]]] = []
        self._items = {"profiles": profiles, "reels": reels}

    def run(self, actor: str, run_input: dict[str, Any], _timeout_s: int) -> ActorRun:
        self.inputs.append((actor, run_input))
        kind = "profiles" if "profile" in actor else "reels"
        return ActorRun(id=kind, status=RunStatus.SUCCEEDED, defaultDatasetId=kind)

    def items(self, run: ActorRun) -> list[dict]:
        return self._items[run.default_dataset_id]


def _posted(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


def test_a_renamed_account_is_read_under_its_new_name():
    """Its old handle may be nobody's, or someone else's, by now."""
    client = _Client(
        profiles=[
            {
                "username": "new_name",
                "inputUrl": "https://www.instagram.com/old_name",
                "followersCount": 5,
            }
        ],
        reels=[
            {
                "shortCode": "A",
                "url": "https://www.instagram.com/p/A/",
                "ownerUsername": "new_name",
                "videoPlayCount": 9,
                "timestamp": _posted(1),
            }
        ],
    )

    read, gave_up = ApifyInstagramSource()._read(client, ["old_name"])  # type: ignore[arg-type]

    assert client.inputs[1][1]["username"] == ["new_name"]
    assert read["old_name"].profile.handle == "new_name"
    assert [p.views for p in read["old_name"].posts] == [9]
    assert gave_up == set()


def test_a_reel_listed_twice_is_one_post():
    reel = {
        "shortCode": "A",
        "url": "https://www.instagram.com/p/A/",
        "ownerUsername": "yuki",
        "videoPlayCount": 9,
        "timestamp": _posted(1),
    }
    client = _Client(
        profiles=[{"username": "yuki", "followersCount": 5}], reels=[reel, reel]
    )

    read, _ = ApifyInstagramSource()._read(client, ["yuki"])  # type: ignore[arg-type]

    assert [p.id for p in read["yuki"].posts] == ["A"]
