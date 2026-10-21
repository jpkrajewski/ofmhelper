from ofmhelpers.instagram.models import GLOBAL_CDN_HOST, Reel


def _reel(thumb: str, video: str | None) -> Reel:
    return Reel(
        code="ABC",
        taken_at=0,
        duration_s=5,
        views=1,
        followers=1,
        thumbnail_url=thumb,
        video_url=video,
    )


def test_regional_cdn_hosts_move_to_the_global_one():
    reel = _reel(
        "https://instagram.ftpa1-2.fna.fbcdn.net/v/t51/x.jpg?oe=1&oh=2",
        "https://scontent-mia3-1.cdninstagram.com/o1/v/y.mp4?oe=1",
    )

    assert reel.thumbnail_url == f"https://{GLOBAL_CDN_HOST}/v/t51/x.jpg?oe=1&oh=2"
    assert reel.video_url == f"https://{GLOBAL_CDN_HOST}/o1/v/y.mp4?oe=1"


def test_non_cdn_and_missing_urls_are_left_alone():
    reel = _reel("https://example.com/a.jpg", None)

    assert reel.thumbnail_url == "https://example.com/a.jpg"
    assert reel.video_url is None
