"""/pictures: presets (a prompt + reference images) and runs -- one Nano
Banana Pro job per picture to recreate, the preset's images first, at the
picture's aspect ratio. The agency's presets are everyone's to use, a
user's own are theirs only. kie.ai is faked at the wire; jobs run inline."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from signin import sign_in, sign_in_agency

from ofmhelpers.db.stores.jobs import get_job
from ofmhelpers.features.assets import store as task_helper_uploads
from ofmhelpers.features.jobs.registry import RESUMABLE
from ofmhelpers.features.pictures import targets as picture_targets
from ofmhelpers.integrations.ai_providers.catalog import get_spec
from ofmhelpers.integrations.ai_providers.kie.client import KieAIClient
from ofmhelpers.integrations.ai_providers.kie.fake import FAKE_KEY
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.image_tools import pictures

PROMPT = "refer guy from first image and second image, recreate third image"


@pytest.fixture
def agency(fake_externals, tmp_path, monkeypatch):
    monkeypatch.setattr(task_helper_uploads, "assets_root", lambda: tmp_path / "assets")
    monkeypatch.setenv("OFM_AIGEN_POLL_INTERVAL_S", "0.01")
    monkeypatch.setenv("OFM_AIGEN_POLL_MAX_INTERVAL_S", "0.02")
    monkeypatch.setenv("OFM_AIGEN_OUT_DIR", str(tmp_path / "out"))
    c = TestClient(app)
    sign_in_agency(c)
    return c


@pytest.fixture
def va(agency):
    c = TestClient(app)
    sign_in(c)
    return c


def _png(name: str, size: tuple[int, int] = (64, 64), color: str = "teal"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return (name, buffer.getvalue(), "image/png")


def _preset(
    client: TestClient, name: str = "danewalker", count: int = 2, aspect: str = ""
) -> dict:
    files = [
        ("images", _png(f"ref{i}.png", color=f"#{i:02x}8080")) for i in range(count)
    ]
    r = client.post(
        "/pictures/presets",
        data={"name": name, "prompt": PROMPT, "aspect_ratio": aspect},
        files=files,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _run(client: TestClient, preset: int, *pictures, provider: str = "kie"):
    files = [("pictures", p) for p in pictures]
    return client.post(
        "/pictures/run",
        data={
            "preset": str(preset),
            "provider": provider,
            "api_key": FAKE_KEY,
            "pictures_manifest": json.dumps([{"kind": "new"}] * len(pictures)),
        },
        files=files,
    )


def test_the_page_is_under_more_and_starts_without_presets(agency):
    html = agency.get("/pictures").text
    assert 'href="/pictures"' in html
    assert 'id="pictures-form"' in html
    assert "No presets yet" in html


def test_a_preset_keeps_its_name_prompt_and_images_in_order(agency):
    preset = _preset(agency)

    assert (preset["name"], preset["prompt"], preset["mine"]) == (
        "danewalker",
        PROMPT,
        True,
    )
    assert [Path(p).name.split("__", 1)[1] for p in preset["images"]] == [
        "ref0.png",
        "ref1.png",
    ]
    assert all(Path(p).is_file() for p in preset["images"])
    html = agency.get("/pictures").text
    assert ">danewalker</option>" in html
    assert f'src="/pictures/presets/{preset["id"]}/images/1"' in html
    thumb = agency.get(f"/pictures/presets/{preset['id']}/images/0")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/webp"


def test_the_agencys_presets_are_everyones_to_run_but_only_its_to_change(agency, va):
    preset = _preset(agency)

    html = va.get("/pictures").text
    assert ">danewalker</option>" in html
    assert 'data-mine="false"' in html
    assert "preset-edit" not in html
    assert va.get(f"/pictures/presets/{preset['id']}/images/0").status_code == 200
    edit = va.post(
        f"/pictures/presets/{preset['id']}",
        data={"name": "x", "prompt": "y", "keep": "0"},
    )
    assert edit.status_code == 403
    assert va.post(f"/pictures/presets/{preset['id']}/delete").status_code == 404


def test_a_users_own_preset_is_theirs_only(agency, va):
    mine = _preset(va, "my look")

    assert ">my look</option>" not in agency.get("/pictures").text
    assert agency.get(f"/pictures/presets/{mine['id']}/images/0").status_code == 404
    assert "preset-edit" in va.get("/pictures").text


def test_a_preset_name_is_unique_per_owner(agency, va):
    _preset(agency)
    files = [("images", _png("a.png"))]
    again = agency.post(
        "/pictures/presets", data={"name": "danewalker", "prompt": "p"}, files=files
    )
    assert again.status_code == 409
    # Another user may use the name.
    assert _preset(va, "danewalker")["name"] == "danewalker"


@pytest.mark.parametrize("count", [0, 11])
def test_a_preset_has_1_to_10_pictures(agency, count):
    files = [("images", _png(f"r{i}.png")) for i in range(count)]
    r = agency.post("/pictures/presets", data={"name": "n", "prompt": "p"}, files=files)
    assert r.status_code == 400
    assert r.json()["detail"] == "A preset has 1 to 10 pictures"


def test_editing_keeps_the_picked_pictures_and_adds_new_ones_after(agency):
    preset = _preset(agency)

    r = agency.post(
        f"/pictures/presets/{preset['id']}",
        data={"name": "dane", "prompt": "new prompt", "keep": "1"},
        files=[("images", _png("added.png", color="red"))],
    )

    assert r.status_code == 200, r.text
    saved = r.json()
    assert (saved["name"], saved["prompt"]) == ("dane", "new prompt")
    assert saved["images"] == [preset["images"][1], saved["images"][1]]
    assert saved["images"][1].endswith("__added.png")


def test_deleting_a_preset_leaves_its_images_for_past_jobs(agency):
    preset = _preset(agency)

    assert agency.post(f"/pictures/presets/{preset['id']}/delete").status_code == 200

    assert "No presets yet" in agency.get("/pictures").text
    assert all(Path(p).is_file() for p in preset["images"])


def test_each_picture_is_its_own_run_with_the_preset_first(va, agency, monkeypatch):
    monkeypatch.setenv("OFM_PICTURES_ASPECT_RATIO", "")  # the picture's own shape
    preset = _preset(agency)

    r = _run(
        va,
        preset["id"],
        _png("tall.png", (900, 1600)),
        _png("wide.png", (1600, 900)),
    )

    assert r.status_code == 202, r.text
    jobs = [get_job(j) for j in r.json()["job_ids"]]
    assert all(j is not None and j.status == "done" for j in jobs), [
        j.error for j in jobs
    ]
    tall, wide = (j.params for j in jobs)
    assert tall["reference_images"][:2] == preset["images"]
    assert tall["reference_images"][2] == tall["pictures"][0]
    assert tall["pictures"][0].endswith("__tall.png")
    assert (tall["prompt"], tall["preset_name"]) == (PROMPT, "danewalker")
    assert (tall["aspect_ratio"], wide["aspect_ratio"]) == ("9:16", "16:9")
    assert tall["resolution"] == "2K"
    assert (tall["provider"], tall["model"]) == ("kie", "kie:nano-banana-pro")


def test_a_preset_too_big_for_the_model_is_refused_before_anything_runs(agency):
    preset = _preset(agency, count=8)  # kie.ai takes 8 in all: 7 + the picture

    r = _run(agency, preset["id"], _png("p.png"))

    assert r.status_code == 400
    assert r.json()["detail"] == (
        "danewalker has 8 pictures; Nano Banana Pro on this provider takes 7"
        " with the new one"
    )


@pytest.mark.parametrize(
    ("data", "detail"),
    [
        ({"provider": "higgsfield"}, "That provider has no Nano Banana Pro"),
        ({"api_key": " "}, "API key is required"),
    ],
)
def test_a_run_needs_a_provider_with_the_model_and_a_key(agency, data, detail):
    preset = _preset(agency)
    form = {"preset": str(preset["id"]), "provider": "kie", "api_key": FAKE_KEY}
    r = agency.post(
        "/pictures/run",
        data={**form, **data, "pictures_manifest": '[{"kind": "new"}]'},
        files=[("pictures", _png("p.png"))],
    )
    assert (r.status_code, r.json()["detail"]) == (400, detail)


def test_a_run_needs_a_picture(agency):
    preset = _preset(agency)
    r = agency.post(
        "/pictures/run",
        data={"preset": str(preset["id"]), "provider": "kie", "api_key": FAKE_KEY},
    )
    assert (r.status_code, r.json()["detail"]) == (
        400,
        "Add 1 to 20 pictures to recreate",
    )


def test_a_preset_another_user_cannot_see_cannot_be_run(agency, va):
    mine = _preset(va, "my look")
    r = _run(agency, mine["id"], _png("p.png"))
    assert r.status_code == 404


def test_the_aspect_is_the_models_nearest_and_its_default_when_unreadable(tmp_path):
    spec = get_spec("kie:nano-banana-pro")
    square = tmp_path / "sq.png"
    Image.new("RGB", (1000, 1010)).save(square)
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")

    assert pictures.aspect_of(str(square), spec, "aspect_ratio") == "1:1"
    assert pictures.aspect_of(str(broken), spec, "aspect_ratio") == "1:1"


def test_recovery_can_rerun_a_picture_job():
    assert RESUMABLE["pictures"] is pictures.run_picture


def test_a_fixed_shape_is_every_runs_whatever_the_picture(agency):
    preset = _preset(agency, aspect="3:4")
    assert preset["aspect_ratio"] == "3:4"
    assert 'data-aspect="3:4"' in agency.get("/pictures").text

    r = _run(agency, preset["id"], _png("wide.png", (1600, 900)))

    [job_id] = r.json()["job_ids"]
    assert get_job(job_id).params["aspect_ratio"] == "3:4"


def test_a_shape_the_model_lacks_is_its_nearest():
    spec = get_spec("kie:nano-banana-pro")  # no 4:5
    assert pictures.run_aspect("4:5", "/unused.png", spec, "aspect_ratio") == "3:4"


def test_an_unknown_shape_is_refused(agency):
    r = agency.post(
        "/pictures/presets",
        data={"name": "n", "prompt": "p", "aspect_ratio": "7:3"},
        files=[("images", _png("a.png"))],
    )
    assert (r.status_code, r.json()["detail"]) == (400, "Unknown picture shape")


def test_editing_sets_and_clears_the_shape(agency):
    preset = _preset(agency)
    form = {"name": "danewalker", "prompt": PROMPT, "keep": ["0", "1"]}

    fixed = agency.post(
        f"/pictures/presets/{preset['id']}", data={**form, "aspect_ratio": "9:16"}
    )
    cleared = agency.post(f"/pictures/presets/{preset['id']}", data=form)

    assert fixed.json()["aspect_ratio"] == "9:16"
    assert cleared.json()["aspect_ratio"] is None


def test_kie_is_asked_for_jpeg_at_2k():
    """User rule 2026-11-01: JPEG, 2K."""
    target = pictures.PICTURE_TARGETS["kie"]
    values = picture_targets.values(
        target,
        {
            "prompt": "p",
            "reference_images": ["/a.png"],
            "aspect_ratio": "3:4",
            "resolution": pictures._resolution(target.spec, target.resolution),
        },
    ).model_dump()

    target.spec.check(values)
    assert (values["output_format"], values["resolution"]) == ("jpg", "2K")
    rh = pictures.PICTURE_TARGETS["runninghub"]
    assert pictures._resolution(rh.spec, rh.resolution) == "2k"


def test_a_reference_under_nano_banana_pros_own_limit_goes_up_untouched(tmp_path):
    """2026-11-01: a 26 MB PNG reference went up as a JPEG copy (kie.ai's
    default cap is 10 MB; Nano Banana Pro takes 30 MB)."""
    big = tmp_path / "face.png"
    Image.frombytes("RGB", (1900, 1900), os.urandom(1900 * 1900 * 3)).save(big)
    assert big.stat().st_size > 10 * 1024 * 1024
    client = KieAIClient("k", out_dir=tmp_path / "out")
    spec = get_spec("kie:nano-banana-pro")

    assert spec.max_upload_bytes == 30_000_000
    assert client._fit_for_upload(big, spec.max_upload_bytes) == big
    assert client._fit_for_upload(big).suffix == ".jpg"


def test_every_picture_is_3_4_unless_its_preset_fixes_another(agency):
    """User rule 2026-11-01: all pictures 3:4."""
    plain = _preset(agency)
    square = _preset(agency, "square", aspect="1:1")

    tall = _run(agency, plain["id"], _png("tall.png", (900, 1600)))
    fixed = _run(agency, square["id"], _png("tall.png", (900, 1600)))

    [tall_job] = tall.json()["job_ids"]
    [fixed_job] = fixed.json()["job_ids"]
    assert get_job(tall_job).params["aspect_ratio"] == "3:4"
    assert get_job(fixed_job).params["aspect_ratio"] == "1:1"
    assert "Default · 3:4" in agency.get("/pictures").text
