"""The Apify source's reading of one key's runs, against a stub client."""

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from ofmhelpers.integrations.apify import ActorRun, DatasetItem, RunStatus
from ofmhelpers.integrations.apify.instagram import ApifyReel
from ofmhelpers.integrations.socials.platforms.instagram_apify import (
    ApifyInstagramSource,
    profile_input,
    reel_input,
)


class _Client:
    """Answers the profile run and the reel run with the given items."""

    key_hint = "...stub"

    def __init__(self, profiles: list[dict], reels: list[dict]) -> None:
        self.inputs: list[tuple[str, dict[str, Any]]] = []
        self._items = {"profiles": profiles, "reels": reels}

    def run(self, actor: str, run_input: BaseModel, _timeout_s: int) -> ActorRun:
        self.inputs.append((actor, run_input.model_dump(by_alias=True)))
        kind = "profiles" if "profile" in actor else "reels"
        return ActorRun(id=kind, status=RunStatus.SUCCEEDED, defaultDatasetId=kind)

    def items(self, run: ActorRun) -> list[DatasetItem]:
        return [
            DatasetItem.model_validate(item)
            for item in self._items[run.default_dataset_id]
        ]


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


def test_a_dataset_item_reads_as_the_actors_fields_it_was_sent_with():
    sent = {"shortCode": "A", "url": "https://www.instagram.com/p/A/", "likesCount": 3}
    item = DatasetItem.model_validate(sent)

    assert not item.failed
    assert item.model_dump(exclude_unset=True) == sent
    assert item.read(ApifyReel).code == "A"


def test_an_error_item_is_told_apart_by_its_error_key():
    item = DatasetItem.model_validate(
        {"url": "https://www.instagram.com/gone", "error": "not_found"}
    )

    assert item.failed
    assert (item.error, item.url) == ("not_found", "https://www.instagram.com/gone")


def test_the_actors_get_their_inputs_under_their_own_names():
    """What `ApifyClient.run` posts: the keys, their order, nothing unset."""
    sent = {"by_alias": True, "exclude_none": True}

    assert list(profile_input(["a", "b"]).model_dump(**sent).items()) == [
        ("usernames", ["a", "b"]),
        ("includeAboutSection", False),
    ]
    assert list(reel_input(["a"], 31, 7).model_dump(**sent).items()) == [
        ("username", ["a"]),
        ("resultsLimit", 31),
        ("onlyPostsNewerThan", "7 days"),
        ("skipPinnedPosts", False),
        ("includeSharesCount", False),
        ("includeTranscript", False),
        ("includeDownloadedVideo", False),
    ]
