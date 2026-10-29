"""The saver-site fallback (downloaders/savers.py), on the wire fakes and
through the live saveinta.com / snapreels.net: a post Instagram's
logged-out API refuses still downloads.

The real half uses a reel verified resolvable on both savers (2026-10-29);
saver sites remove posts too, so if it stops resolving, pick a fresh one.
"""

from ofmhelpers.downloaders import generic, savers

REEL = "https://www.instagram.com/reel/Dc-i7hPgI7_/"


def test_a_refused_post_downloads_through_a_saver(backend, tmp_path):
    result = savers.download(REEL, tmp_path)

    assert result.success, result.error
    assert len(result.output_paths) == 1
    path = result.output_paths[0]
    assert generic.is_video_file(path)
    assert path.stat().st_size > 0


def test_a_non_instagram_link_is_refused(backend, tmp_path):
    result = savers.download("https://www.tiktok.com/@a/video/1", tmp_path)

    assert not result.success
    assert "Unsupported URL" in (result.error or "")
