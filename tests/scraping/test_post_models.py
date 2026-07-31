"""Normalization of raw Apify dataset items into the post models.

Apify's actors are third-party and their field names drift (`timestamp` vs
`takenAt` vs `date`, hashtags as strings vs objects), which is why the mapping
is this fussy. These tests pin the exact mapping so it survives being moved
onto the model.
"""

from datetime import UTC, datetime

from ofmhelpers.scraping.models import PostBase, Reel, TikTokAuthor, TikTokVideo


class TestReel:
    def test_maps_a_full_instagram_item(self):
        reel = Reel.from_apify(
            {
                "ownerUsername": "someone",
                "url": "https://instagram.com/reel/abc",
                "timestamp": "2026-07-01T12:00:00+00:00",
                "videoPlayCount": 40_000,
                "likesCount": 900,
                "commentsCount": 12,
                "caption": "a caption",
                "videoDuration": 14.5,
                "hashtags": ["one", {"name": "two"}],
            }
        )
        assert reel.username == "someone"
        assert reel.url == "https://instagram.com/reel/abc"
        assert reel.timestamp == datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
        assert (reel.views, reel.likes, reel.comments) == (40_000, 900, 12)
        assert reel.caption == "a caption"
        assert reel.duration_seconds == 14.5
        # hashtags arrive as bare strings from some actors and {"name": ...}
        # objects from others; both flatten to the name.
        assert reel.hashtags == ["one", "two"]

    def test_falls_back_through_every_alias(self):
        reel = Reel.from_apify(
            {"shortCode": "abc", "playCount": 10, "likes": 2, "comments": 1}
        )
        assert reel.url == "abc"
        assert (reel.views, reel.likes, reel.comments) == (10, 2, 1)

    def test_unix_timestamp_is_read_as_utc(self):
        reel = Reel.from_apify({"takenAt": 1_780_000_000})
        assert reel.timestamp == datetime.fromtimestamp(1_780_000_000, tz=UTC)

    def test_unparseable_timestamp_falls_back_to_now(self):
        """A bad date must not lose the whole row -- the sheet is a review
        artifact, and one wrong date beats one missing post."""
        before = datetime.now(UTC)
        reel = Reel.from_apify({"date": "not a date"})
        assert before <= reel.timestamp <= datetime.now(UTC)

    def test_caption_is_truncated_and_hashtags_capped(self):
        reel = Reel.from_apify(
            {"caption": "x" * 500, "hashtags": [str(i) for i in range(20)]}
        )
        assert len(reel.caption or "") == 200
        assert len(reel.hashtags) == 10

    def test_missing_everything_is_still_a_row_but_not_a_valid_one(self):
        reel = Reel.from_apify({})
        assert (reel.username, reel.url) == ("", "")
        assert reel.views is None
        assert not reel.is_valid()

    def test_is_valid_needs_a_username_a_url_and_views(self):
        item = {"ownerUsername": "someone", "url": "u", "videoPlayCount": 1}
        assert Reel.from_apify(item).is_valid()
        assert not Reel.from_apify({**item, "videoPlayCount": 0}).is_valid()


class TestTikTokVideo:
    RAW = {
        "id": 12345,
        "input": "someone",
        "text": "a caption",
        "webVideoUrl": "https://tiktok.com/@someone/video/12345",
        "createTimeISO": "2026-07-01T12:00:00+00:00",
        "playCount": 40_000,
        "diggCount": 900,
        "commentCount": 12,
        "shareCount": 3,
        "collectCount": 4,
        "repostCount": 5,
        "hashtags": [{"name": "one"}, {"name": ""}, {}],
        "authorMeta": {
            "id": "a1",
            "name": "someone",
            "nickName": "Some One",
            "profileUrl": "https://tiktok.com/@someone",
            "verified": True,
            "fans": 1000,
            "following": 10,
            "heart": 5000,
            "video": 42,
            "signature": "bio",
            "originalAvatarUrl": "https://cdn/avatar.jpg",
        },
        "musicMeta": {
            "musicName": "song",
            "musicAuthor": "artist",
            "musicOriginal": True,
            "playUrl": "https://cdn/song.mp3",
        },
        "videoMeta": {
            "duration": 15,
            "originalCoverUrl": "https://cdn/cover.jpg",
            "definition": "1080p",
            "format": "mp4",
        },
        "isAd": False,
        "isPinned": True,
        "textLanguage": "en",
    }

    def test_maps_a_full_tiktok_item(self):
        video = TikTokVideo.from_apify(self.RAW)
        assert video.id == "12345"  # stringified: Apify sends it as a number
        assert video.username == "someone"
        assert video.timestamp == datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
        assert (video.views, video.likes, video.comments) == (40_000, 900, 12)
        assert (video.shares, video.bookmarks, video.reposts) == (3, 4, 5)
        assert video.duration_seconds == 15
        # only named hashtags survive
        assert video.hashtags == ["one"]
        assert video.music_name == "song"
        assert video.cover_url == "https://cdn/cover.jpg"
        assert (video.resolution, video.format) == ("1080p", "mp4")
        assert video.is_pinned
        assert not video.is_ad
        assert video.language == "en"

    def test_author_is_nested_and_renamed(self):
        author = TikTokVideo.from_apify(self.RAW).author
        assert isinstance(author, TikTokAuthor)
        assert (author.username, author.nickname) == ("someone", "Some One")
        assert (author.followers, author.total_likes, author.total_videos) == (
            1000,
            5000,
            42,
        )
        assert author.verified
        assert author.avatar_url == "https://cdn/avatar.jpg"

    def test_author_falls_back_to_the_short_avatar_key(self):
        raw = {**self.RAW, "authorMeta": {"name": "x", "avatar": "https://cdn/a.jpg"}}
        assert TikTokVideo.from_apify(raw).author.avatar_url == "https://cdn/a.jpg"

    def test_unix_create_time_is_used_when_there_is_no_iso_one(self):
        raw = {k: v for k, v in self.RAW.items() if k != "createTimeISO"}
        raw["createTime"] = 1_780_000_000
        assert TikTokVideo.from_apify(raw).timestamp == datetime.fromtimestamp(
            1_780_000_000, tz=UTC
        )

    def test_username_falls_back_to_the_author_when_the_input_is_absent(self):
        raw = {k: v for k, v in self.RAW.items() if k != "input"}
        assert TikTokVideo.from_apify(raw).username == "someone"

    def test_empty_item_yields_zeroed_counters_not_an_error(self):
        video = TikTokVideo.from_apify({})
        assert (video.views, video.likes, video.comments) == (0, 0, 0)
        assert video.hashtags == []
        assert not video.is_valid()


def test_both_post_types_share_the_exporter_contract():
    """post_exporter writes any PostBase, so the shared fields have to exist on
    both with the same names."""
    assert issubclass(Reel, PostBase)
    assert issubclass(TikTokVideo, PostBase)
