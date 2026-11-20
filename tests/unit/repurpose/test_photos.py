import json
import random
import subprocess
from pathlib import Path

import pytest
from PIL import ExifTags, Image
from pydantic import ValidationError

from ofmhelpers.media.repurpose.photos import _Video, write_photos
from ofmhelpers.media.repurpose.photos import models as photo_models
from ofmhelpers.media.repurpose.photos.distinct import apart, pdq, same_picture_bits
from ofmhelpers.media.repurpose.photos.models import (
    PhotoPreset,
    load_photo_preset,
    photo_preset_names,
)

THREADS = json.loads(
    Path(photo_models.__file__).with_name("threads.json").read_text(encoding="utf-8")
)


def _clip(path: Path, seconds: float, *, audio_s: float | None = None) -> Path:
    args = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi"]
    args += ["-i", f"testsrc2=size=180x320:rate=30:duration={seconds}"]
    if audio_s is not None:
        args += ["-f", "lavfi", "-i", f"sine=duration={audio_s}"]
    subprocess.run([*args, "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def _preset(tmp_path: Path, **lens: object) -> str:
    raw = json.loads(json.dumps(THREADS))
    raw["pictures"] = 4
    raw["lens"].update({"short_side": 256, **lens})
    raw["stills"]["hash_side"] = 64
    path = tmp_path / "small.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("name", photo_preset_names())
def test_every_preset_loads(name):
    assert load_photo_preset(name).pictures >= 1


@pytest.mark.parametrize(
    ("where", "value"),
    [
        (("retouch", "crops"), [0.6]),
        (("retouch", "crops"), []),
        (("retouch", "grain"), -1),
        (("lens", "iso"), [400, 64]),
        (("lens", "exposures"), ["1/0"]),
        (("lens", "exposures"), ["fast"]),
        (("stills", "slice"), [0.9, 0.1]),
        (("lens", "grian"), 2),
        (("device",), "strip"),
    ],
)
def test_a_value_that_would_fail_a_job_is_refused_when_loaded(where, value):
    raw = json.loads(json.dumps(THREADS))
    *parents, last = where
    holder = raw
    for key in parents:
        holder = holder[key]
    holder[last] = value

    with pytest.raises(ValidationError):
        PhotoPreset.model_validate(raw)


def test_a_preset_is_a_name_or_a_path_and_an_unknown_one_names_the_known(tmp_path):
    assert load_photo_preset(_preset(tmp_path)).pictures == 4
    with pytest.raises(ValueError, match="known: threads"):
        load_photo_preset("instagram")


@pytest.mark.parametrize("seed", ["a", "b", "c", "d", "e", "f"])
def test_no_still_is_asked_for_past_the_last_frame(tmp_path, seed):
    clip = _clip(tmp_path / "v.mp4", 1, audio_s=1.4)
    how = load_photo_preset("threads").stills

    stills = _Video.of(clip, 30).stills(30, how, random.Random(seed))  # noqa: S311 - not cryptography

    assert len(stills) >= 25
    assert max(s.at for s in stills) < 29 / 30


def test_a_set_is_different_pictures_with_the_phones_exif(tmp_path):
    clip = _clip(tmp_path / "v.mp4", 3)
    dest = [tmp_path / "out" / f"IMG_{i}.JPG" for i in range(4)]

    written = write_photos(
        clip, dest, preset=_preset(tmp_path), seed="s1", timeout_s=30
    )

    assert 1 <= len(written) <= 4
    hashes = []
    for photo in written:
        with Image.open(photo.path) as img:
            exif = img.getexif()
            assert (exif[ExifTags.Base.Make], exif[ExifTags.Base.Model]) == (
                "Apple",
                "iPhone 16 Pro Max",
            )
            hashes.append(pdq(img.convert("RGB"), 64))
    for i, a in enumerate(hashes):
        for b in hashes[i + 1 :]:
            assert apart(a, b) > same_picture_bits()


def test_the_same_seed_makes_the_same_set(tmp_path):
    clip = _clip(tmp_path / "v.mp4", 3)
    preset = _preset(tmp_path)

    def made(folder: str, seed: str) -> list[tuple[float, float]]:
        dest = [tmp_path / folder / f"{i}.JPG" for i in range(4)]
        written = write_photos(clip, dest, preset=preset, seed=seed, timeout_s=30)
        return [(p.at, p.crop) for p in written]

    assert made("a", "s") == made("b", "s")
    assert made("a", "s") != made("c", "t")
