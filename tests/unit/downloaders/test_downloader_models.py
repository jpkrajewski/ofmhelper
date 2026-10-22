"""yt-dlp's two dict shapes as models: the options it is given and the info
it returns."""

from pathlib import Path

from ofmhelpers.downloaders.generic import DownloadConfig
from ofmhelpers.downloaders.models import YtDlpInfo


def test_info_reads_merged_files_of_a_single_video():
    info = {
        "requested_downloads": [{"filepath": "/d/a.mp4"}, {"_filename": "/d/b.mp4"}]
    }
    assert YtDlpInfo.model_validate(info).output_paths() == [
        Path("/d/a.mp4"),
        Path("/d/b.mp4"),
    ]


def test_info_reads_every_playlist_entry_and_skips_missing_ones():
    info = {
        "_type": "playlist",
        "entries": [{"filepath": "/d/1.mp4"}, None, {"requested_downloads": []}],
        "id": "ignored-extra-field",
    }
    assert YtDlpInfo.model_validate(info).output_paths() == [Path("/d/1.mp4")]


def test_options_dict_keeps_yt_dlp_defaults_for_unset_options():
    config = DownloadConfig(
        cookies_from_browser=None, youtube_player_clients=["ios"], extractor_args={}
    )

    opts = config.ydl_opts().to_ydl()

    assert opts["noplaylist"] is False
    assert opts["extractor_args"] == {"youtube": {"player_client": ["ios"]}}
    assert "cookiefile" not in opts
    assert "cookiesfrombrowser" not in opts


def test_options_merge_pot_provider_args_over_player_clients():
    pot = {"youtubepot-bgutilhttp": {"base_url": ["http://pot:4416"]}}
    config = DownloadConfig(
        cookies_from_browser="firefox",
        youtube_player_clients=["ios"],
        extractor_args=pot,
    )

    opts = config.ydl_opts("/c/cookies.txt").to_ydl()

    assert opts["extractor_args"] == {"youtube": {"player_client": ["ios"]}, **pot}
    assert opts["cookiefile"] == "/c/cookies.txt"  # the file wins over a browser
    assert "cookiesfrombrowser" not in opts
