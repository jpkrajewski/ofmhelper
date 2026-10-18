"""/generate and /ai: every provider's models through one form and one job
type. The fake provider runs real jobs end to end for free; kie.ai and
Higgsfield are exercised with their single calls stubbed, so every request
goes through the same router, runner and result handling as a paid run."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import io
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ofmhelpers.aigenproviders import base
from ofmhelpers.aigenproviders.base import Provider, TaskState
from ofmhelpers.aigenproviders.catalog import catalog
from ofmhelpers.aigenproviders.errors import ProviderUnavailableError
from ofmhelpers.aigenproviders.higgsfield.provider import HiggsfieldProvider
from ofmhelpers.aigenproviders.kaiai.provider import KieProvider
from ofmhelpers.web.db.repositories import JobRepository
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.downloads import videos as download_videos_router
from ofmhelpers.web.routers.task_helpers import job_status_payload
from ofmhelpers.web.routers.task_helpers import uploads as task_helper_uploads
from ofmhelpers.web.schemas import JobPreview
from ofmhelpers.web.stores.jobs import create_job, get_job, set_job_preview

NEW = json.dumps([{"kind": "new"}])
FAKE_IMAGE = "fake:image"


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (300, 300), color="red").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("OFM_AIGEN_OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("OFM_AIGEN_POLL_INTERVAL_S", "0")
    assets = tmp_path / "assets"
    assets.mkdir()
    monkeypatch.setattr(task_helper_uploads, "ASSETS_ROOT", assets)
    return assets


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


def _fake(client, files=None, **fields):
    data = {
        "provider": "fake",
        "model": FAKE_IMAGE,
        "prompt": "a cat",
        "delay": "0",
        **fields,
    }
    return client.post("/ai/run", data=data, files=files)


# --- the page -------------------------------------------------------------


def test_page_offers_every_provider_and_every_model(client):
    html = client.get("/generate").text

    for name in ("kie", "higgsfield", "fake"):
        assert f'<option value="{name}"' in html
    models = json.loads(
        re.search(
            r'id="model-catalog" type="application/json">(.*?)</script>',
            html,
            re.DOTALL,
        ).group(1)
    )
    assert {m["key"] for m in models} == set(catalog())
    assert 'action="/ai/run"' in html


def test_page_prefills_each_providers_key_for_the_role(client, monkeypatch):
    monkeypatch.setenv("KIE_AI_API_KEY_ADMIN", "kie-admin")
    monkeypatch.setenv("HF_API_KEY_ADMIN", "hf-id:hf-secret")

    html = client.get("/generate").text

    keys = json.loads(
        re.search(r'data-keys="([^"]*)"', html).group(1).replace("&#34;", '"')
    )
    assert keys["kie"] == "kie-admin"
    assert keys["higgsfield"] == "hf-id:hf-secret"


def test_prompt_box_is_shared_outside_the_model_fields(client):
    html = client.get("/generate").text

    assert re.search(r'<textarea name="prompt"', html)
    assert '<fieldset class="tool-fields" id="model-fields"></fieldset>' in html


def test_kling_and_nano_banana_default_prompts_ship_in_the_catalog(client):
    html = client.get("/generate").text

    assert "Handheld camera movement, slight natural shake" in html
    assert "Refer to first image and second image, replicate third image." in html


# --- /ai/fields -----------------------------------------------------------


def test_fields_render_the_schema_as_inputs(client):
    html = client.get("/ai/fields", params={"model": "kie:wan/3-0-video"}).text

    assert re.search(
        r'<select name="resolution".*?<option value="1080P" selected', html, re.DOTALL
    )
    assert re.search(
        r'<input type="number" name="duration".*?min="2.0".*?max="30.0"',
        html,
        re.DOTALL,
    )
    assert '<input type="hidden" name="audio" value="false">' in html
    assert re.search(r'name="audio" value="true" checked', html)
    for field in (
        "reference_image_urls",
        "reference_video_urls",
        "reference_audio_urls",
    ):
        assert f'data-field="{field}"' in html
    assert 'name="prompt"' not in html, "the page's own prompt box is used"


def test_a_single_file_field_is_a_one_item_picker(client):
    html = client.get(
        "/ai/fields", params={"model": "kie:minimax-h3/image-to-video"}
    ).text

    assert re.search(
        r'data-field="first_frame_url" data-kind="image" data-max="1"', html
    )


def test_fields_of_an_unknown_model_404(client):
    assert client.get("/ai/fields", params={"model": "kie:nope"}).status_code == 404


# --- /ai/run with the fake provider (real jobs, no credits) ---------------


def test_a_fake_image_lands_in_the_shared_out_dir_and_the_asset_store(client, tmp_path):
    job_id = _fake(client).json()["job_id"]

    job = get_job(job_id)
    assert job.status == "done"
    [result] = job.result_files()
    assert Path(result.path).parent == tmp_path / "out"
    assert result.remote_url is None, "a fake result has no hosted copy"
    assert result.name in [f["name"] for f in client.get("/refs?kind=image").json()]


def test_the_error_outcome_fails_with_the_message_typed_in(client):
    job_id = _fake(client, outcome="error", error_message="nope, on purpose").json()[
        "job_id"
    ]

    job = get_job(job_id)
    assert (job.status, job.error) == ("failed", "nope, on purpose")


def test_references_are_stored_as_paths_under_their_schema_field(client, _isolated):
    files = {"reference_image_urls": ("ref.png", io.BytesIO(_png()), "image/png")}

    job_id = _fake(client, files=files, reference_image_urls_manifest=NEW).json()[
        "job_id"
    ]

    params = get_job(job_id).params
    assert params["provider"] == "fake"
    assert params["model"] == FAKE_IMAGE
    [path] = params["reference_image_urls"]
    assert Path(path).parent == _isolated


def test_a_reused_reference_is_not_uploaded_again(client, _isolated):
    files = {"reference_image_urls": ("ref.png", io.BytesIO(_png()), "image/png")}
    first = _fake(client, files=files, reference_image_urls_manifest=NEW).json()
    [path] = get_job(first["job_id"]).params["reference_image_urls"]

    reuse = json.dumps([{"kind": "existing", "path": path}])
    second = _fake(client, reference_image_urls_manifest=reuse).json()

    assert get_job(second["job_id"]).params["reference_image_urls"] == [path]
    assert len(list(_isolated.glob("*__ref.png"))) == 1


def test_settings_are_typed_from_the_form(client):
    job_id = _fake(client, delay="0", outcome="success").json()["job_id"]

    assert get_job(job_id).params["delay"] == 0


# --- /ai/run validation ---------------------------------------------------


@pytest.mark.parametrize(
    ("data", "detail"),
    [
        ({"provider": "kie", "model": "kie:nope", "api_key": "k"}, "Unknown model"),
        (
            {"provider": "higgsfield", "model": "kie:wan/3-0-video", "api_key": "k"},
            "Unknown model",
        ),
        ({"provider": "kie", "model": "kie:wan/3-0-video", "prompt": "p"}, "API key"),
        (
            {
                "provider": "kie",
                "model": "kie:gpt-image-2-5-flare-image-to-image",
                "api_key": "k",
                "prompt": "p",
            },
            "input_urls",
        ),
        (
            {
                "provider": "kie",
                "model": "kie:wan/3-0-video",
                "api_key": "k",
                "prompt": "p",
                "resolution": "8K",
            },
            "resolution",
        ),
        (
            {
                "provider": "kie",
                "model": "kie:wan/3-0-video",
                "api_key": "k",
                "prompt": "p",
                "duration": "soon",
            },
            "duration",
        ),
    ],
)
def test_a_bad_request_is_a_400_and_queues_nothing(client, data, detail):
    r = client.post("/ai/run", data=data)

    assert r.status_code == 400
    assert detail in r.json()["detail"]


# --- real providers, single calls stubbed ---------------------------------


@pytest.fixture
def kie(monkeypatch):
    """kie.ai answering every call; `calls` records what was sent."""
    calls = {"submit": [], "upload": []}

    def submit(_self, spec, payload, idempotency_key):
        calls["submit"].append((spec.model_id, payload))
        return "kie-task"

    def upload(_self, path):
        calls["upload"].append(path.name)
        return f"https://up.kie/{path.name}"

    def download(_self, url, dest):
        dest.write_bytes(b"video")
        return dest

    monkeypatch.setattr(KieProvider, "submit", submit)
    monkeypatch.setattr(KieProvider, "upload", upload)
    monkeypatch.setattr(
        KieProvider,
        "status",
        lambda _self, _id: TaskState(state="done", urls=["https://cdn.kie/out.mp4"]),
    )
    monkeypatch.setattr(KieProvider, "download", download)
    return calls


def test_a_kie_run_sends_the_catalog_body_and_keeps_the_hosted_copy(client, kie):
    files = {"reference_image_urls": ("face.png", io.BytesIO(_png()), "image/png")}
    r = client.post(
        "/ai/run",
        data={
            "provider": "kie",
            "model": "kie:wan/3-0-video",
            "api_key": "k",
            "prompt": "dance",
            "duration": "7",
            "audio": "false",
            "reference_image_urls_manifest": NEW,
        },
        files=files,
    )

    [(model_id, payload)] = kie["submit"]
    assert model_id == "wan/3-0-video"
    assert payload == {
        "nsfw_checker": False,
        "prompt": "dance",
        "duration": 7,
        "audio": False,
        "reference_image_urls": [f"https://up.kie/{kie['upload'][0]}"],
    }
    job = get_job(r.json()["job_id"])
    assert job.status == "done"
    assert job.result_files()[0].remote_url == "https://cdn.kie/out.mp4"


def test_a_failed_download_keeps_the_job_on_the_hosted_copy(client, kie, monkeypatch):
    def broken(_self, url, dest):
        msg = "download failed"
        raise ProviderUnavailableError(msg)

    monkeypatch.setattr(KieProvider, "download", broken)

    job_id = client.post(
        "/ai/run",
        data={
            "provider": "kie",
            "model": "kie:wan/3-0-video",
            "api_key": "k",
            "prompt": "p",
        },
    ).json()["job_id"]

    job = get_job(job_id)
    assert job.status == "done"
    [result] = job.result_files()
    assert (result.path, result.remote_url) == (None, "https://cdn.kie/out.mp4")


def test_a_failure_before_any_result_fails_the_job(client, kie, monkeypatch):
    monkeypatch.setattr(
        KieProvider,
        "status",
        lambda _self, _id: TaskState(state="failed", error="content flagged"),
    )

    job_id = client.post(
        "/ai/run",
        data={
            "provider": "kie",
            "model": "kie:wan/3-0-video",
            "api_key": "k",
            "prompt": "p",
        },
    ).json()["job_id"]

    job = get_job(job_id)
    assert job.status == "failed"
    assert "content flagged" in job.error


def test_a_higgsfield_run_uses_its_own_field_names(client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        HiggsfieldProvider,
        "submit",
        lambda _self, spec, payload, idempotency_key: (
            sent.append((spec.model_id, payload)) or "r1"
        ),
    )
    monkeypatch.setattr(
        HiggsfieldProvider, "upload", lambda _self, p: f"https://hf/{p.name}"
    )
    monkeypatch.setattr(
        HiggsfieldProvider,
        "status",
        lambda _self, _id: TaskState(state="done", urls=["https://hf/out.png"]),
    )
    monkeypatch.setattr(
        HiggsfieldProvider,
        "download",
        lambda _self, _u, dest: dest.write_bytes(b"x") and dest,
    )
    files = {"image_url": ("face.png", io.BytesIO(_png()), "image/png")}

    r = client.post(
        "/ai/run",
        data={
            "provider": "higgsfield",
            "model": "higgsfield:kling-video/v3.0/pro/image-to-video",
            "api_key": "id:secret",
            "prompt": "p",
            "image_url_manifest": NEW,
        },
        files=files,
    )

    assert r.status_code == 200, r.text
    [(model_id, payload)] = sent
    assert model_id == "kling-video/v3.0/pro/image-to-video"
    assert payload["image_url"].startswith("https://hf/"), "one file, sent as one URL"


# --- job pages, gallery, legacy jobs -------------------------------------


def test_job_page_shows_the_model_settings_and_reference_preview(client):
    files = {"reference_image_urls": ("myref.png", io.BytesIO(_png()), "image/png")}
    job_id = _fake(client, files=files, reference_image_urls_manifest=NEW).json()[
        "job_id"
    ]

    html = client.get(f"/ai/jobs/{job_id}").text

    assert "job-inputs" in html
    match = re.search(r'/refs/thumb\?path=[^"&]+', html)
    assert match, "no reference thumbnail on the page"
    assert client.get(match.group(0)).status_code == 200


def test_a_plain_download_job_page_has_no_inputs_section(client, monkeypatch):
    monkeypatch.setattr(download_videos_router, "_run_downloads", lambda urls: [])
    job_id = client.post(
        "/download-videos/run", data={"urls": "https://a.example/1"}
    ).json()["job_id"]

    assert "job-inputs" not in client.get(f"/download-videos/jobs/{job_id}").text


def test_gallery_cards_name_model_and_provider_and_offer_recreate(client):
    done_id = _fake(client).json()["job_id"]
    failed_id = _fake(client, outcome="error").json()["job_id"]

    html = client.get("/generate").text

    for job_id in (done_id, failed_id):
        card = re.search(rf'data-job-id="{job_id}"(.*?)</p>', html, re.DOTALL)
        assert card, f"no card for {job_id}"
        assert "recreate-btn" in card.group(1) or "recreate-btn" in html
    assert "Fake image (testing) · Fake AI (testing)" in html


def test_a_running_card_resumes_polling_under_ai(client):
    job_id = create_job("ai", {"provider": "fake", "model": FAKE_IMAGE, "prompt": "p"})

    card = re.search(
        rf'data-job-id="{job_id}"(.*?)>', client.get("/generate").text, re.DOTALL
    )

    assert "data-pending" in card.group(1)
    assert 'data-poll-prefix="/ai"' in card.group(1)


def test_a_job_from_before_ai_still_shows_and_opens(client, tmp_path):
    out = tmp_path / "old.png"
    out.write_bytes(_png())
    job_id = create_job("seedance", {"prompt": "old run", "reference_images": []})
    JobRepository().update_status(
        job_id, "done", result=[{"name": "old.png", "path": str(out)}]
    )

    assert f'data-job-id="{job_id}"' in client.get("/generate").text
    assert client.get(f"/seedance/jobs/{job_id}").status_code == 200
    assert client.get(f"/seedance/files/{job_id}/0").status_code == 200


def test_status_json_surfaces_the_preview_only_while_running(tmp_path):
    job_id = create_job("ai", {"provider": "kie", "model": "kie:nano-banana-pro"})
    set_job_preview(job_id, JobPreview(remote_url="https://cdn/x.png", kind="image"))

    assert job_status_payload(get_job(job_id), "/ai/files").preview is not None

    (tmp_path / "x.png").write_bytes(b"x")
    JobRepository().update_status(
        job_id, "done", result=[{"name": "x.png", "path": str(tmp_path / "x.png")}]
    )
    assert job_status_payload(get_job(job_id), "/ai/files").preview is None


# --- a provider added later ----------------------------------------------


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    path = tmp_path / "plugin-catalog.json"
    path.write_text(
        json.dumps(
            [
                {
                    "model_id": "toy",
                    "label": "Toy",
                    "family": "Toy",
                    "kind": "image",
                    "input_schema": {
                        "type": "object",
                        "required": ["prompt"],
                        "properties": {
                            "prompt": {"type": "string"},
                            "steps": {"type": "integer", "default": 4},
                        },
                    },
                }
            ]
        )
    )
    sent = []

    class PluginProvider(Provider):
        name = "plugin"
        label = "Plugin"

        @classmethod
        def catalog_path(cls):
            return path

        @classmethod
        def default_key(cls, admin):
            del admin

        def upload(self, p):
            return str(p)

        def submit(self, spec, payload, idempotency_key):
            del spec, idempotency_key
            sent.append(payload)
            return "t"

        def status(self, task_id):
            del task_id
            return TaskState(state="done", urls=["https://plugin/out.png"])

        def download(self, url, dest):
            del url
            dest.write_bytes(b"x")
            return dest

    monkeypatch.setitem(base._registry(), "plugin", PluginProvider)
    catalog.cache_clear()
    yield sent
    catalog.cache_clear()


def test_a_new_provider_works_end_to_end_with_no_web_change(client, plugin):
    html = client.get("/generate").text
    assert '<option value="plugin"' in html
    assert "steps" in client.get("/ai/fields", params={"model": "plugin:toy"}).text

    r = client.post(
        "/ai/run",
        data={
            "provider": "plugin",
            "model": "plugin:toy",
            "api_key": "k",
            "prompt": "p",
            "steps": "8",
        },
    )

    assert get_job(r.json()["job_id"]).status == "done"
    assert plugin == [{"prompt": "p", "steps": 8}]
