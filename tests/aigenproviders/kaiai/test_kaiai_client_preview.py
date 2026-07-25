"""
Covers KieAIClient's on_result_urls hook: kie.ai's hosted result is already
live the moment poll_task reports success, well before download_urls has
pulled it down locally. The per-model wrappers (generate_image_nbp,
generate_video_seedance2, generate_video_kling3) must call this callback
right after the poll succeeds and before starting the download, so a caller
(the web app) can show the hosted URL immediately instead of blocking on the
download. All network calls are stubbed -- see test_recovery.py for the same
pattern.
"""

import pytest

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient


@pytest.fixture
def client(tmp_path):
    return KieAIClient(
        api_key="test-key",
        out_dir=tmp_path / "out",
        task_log=tmp_path / "tasks.jsonl",
        completions_log=tmp_path / "completions.jsonl",
        resolved_log=tmp_path / "resolved.jsonl",
    )


def _stub_lifecycle(client, monkeypatch, urls, calls):
    monkeypatch.setattr(client, "create_task", lambda *a, **k: "t1")
    monkeypatch.setattr(client, "poll_task", lambda *a, **k: urls)
    monkeypatch.setattr(
        client,
        "download_urls",
        lambda urls, tid, ext: calls.append(("download_urls", urls))
        or [client.OUT_DIR / f"{tid}.{ext}"],
    )


def test_generate_image_nbp_calls_on_result_urls_before_downloading(
    client, monkeypatch
):
    calls = []
    _stub_lifecycle(client, monkeypatch, ["https://cdn.kie.ai/img.png"], calls)

    def on_result_urls(urls):
        calls.append(("on_result_urls", urls))

    out_path = client.generate_image_nbp(prompt="p", on_result_urls=on_result_urls)

    assert calls == [
        ("on_result_urls", ["https://cdn.kie.ai/img.png"]),
        ("download_urls", ["https://cdn.kie.ai/img.png"]),
    ]
    assert out_path == client.OUT_DIR / "t1.png"


def test_generate_image_nbp_works_without_on_result_urls(client, monkeypatch):
    """The callback is optional -- every existing caller that doesn't pass
    one must keep working exactly as before."""
    _stub_lifecycle(client, monkeypatch, ["https://cdn.kie.ai/img.png"], [])

    out_path = client.generate_image_nbp(prompt="p")

    assert out_path == client.OUT_DIR / "t1.png"


def test_generate_video_kling3_calls_on_result_urls_before_downloading(
    client, monkeypatch
):
    calls = []
    _stub_lifecycle(client, monkeypatch, ["https://cdn.kie.ai/clip.mp4"], calls)

    def on_result_urls(urls):
        calls.append(("on_result_urls", urls))

    client.generate_video_kling3(prompt="p", on_result_urls=on_result_urls)

    assert [c[0] for c in calls] == ["on_result_urls", "download_urls"]


def test_generate_video_seedance2_calls_on_result_urls_before_downloading(
    client, monkeypatch
):
    calls = []
    _stub_lifecycle(client, monkeypatch, ["https://cdn.kie.ai/clip.mp4"], calls)

    def on_result_urls(urls):
        calls.append(("on_result_urls", urls))

    client.generate_video_seedance2(prompt="p", on_result_urls=on_result_urls)

    assert [c[0] for c in calls] == ["on_result_urls", "download_urls"]
