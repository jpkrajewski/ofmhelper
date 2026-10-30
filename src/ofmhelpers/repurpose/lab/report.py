"""After a search: what worked, as files a person reads, a CLI:

    python -m ofmhelpers.repurpose.lab.report CLIPS_DIR OUT_DIR --workers 16

From OUT/results.jsonl it writes OUT/report/:
- `knobs.csv` -- each knob alone (ablation): % found per method, VMAF,
  sound change, per strength;
- `trials.csv` -- every random throw and search trial, means over its clips;
- `presets/stealth_*.json` -- three picks (video part + audio part each,
  `strip` device), loadable by `Repurpose.apply_json_preset(path)` and
  droppable into repurpose/presets/;
- `validation.csv` -- those presets on every clip x 2 seeds;
- `examples/*.mp4` -- source | copy side by side, the copy's sound;
- `index.html` -- all of it on one page.
"""

import argparse
import csv
import html
import json
import subprocess
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from statistics import mean
from typing import Any

from pydantic import BaseModel

from ofmhelpers.repurpose.lab.measure import Measurement, measure
from ofmhelpers.repurpose.lab.space import BY_NAME, Part, preset
from ofmhelpers.utils.ffmpeg import binary

_METHODS = ("pdq", "phash", "dhash", "whash", "sscd", "tmk")
# Video picks: the lowest caught % at or above this much under an untouched
# copy's VMAF (the device's encode alone costs ~5; VMAF capped there).
_VIDEO_PICKS = {"safe": 1.0, "balanced": 3.0, "wild": 8.0}
# Audio picks: the lowest Chromaprint % within this much more sound change
# than an untouched copy (dB); wild: any.
_AUDIO_PICKS = {"safe": 1.5, "balanced": 3.0, "wild": 99.0}
_VALIDATION_SEEDS = (11, 12)
_EXAMPLE_CLIPS = 2
# A method that finds this much of a copy caught it (checker.CAUGHT_SHARE).
CAUGHT_PERCENT = 50


class TrialRow(BaseModel):
    trial: str
    phase: str
    part: Part
    strengths: dict[str, float]
    clips: int
    errors: int
    caught: float
    methods: dict[str, float]
    audio: float | None
    vmaf: float | None
    change_db: float | None
    over_cap: bool


def _load(results: Path) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in results.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["trial"]].append(row)
    return rows


def _avg(values: list[float | None]) -> float | None:
    got = [v for v in values if v is not None]
    return round(mean(got), 2) if got else None


def _trial(name: str, rows: list[dict[str, Any]]) -> TrialRow:
    ok = [r for r in rows if not r["error"]]
    strengths = rows[0]["strengths"]
    return TrialRow(
        trial=name,
        phase=rows[0]["phase"],
        part=rows[0]["part"],
        strengths=strengths,
        clips=len(rows),
        errors=len(rows) - len(ok),
        caught=_avg([max(r["video"].values(), default=0) for r in ok]) or 0.0,
        methods={
            m: v
            for m in _METHODS
            if (v := _avg([r["video"].get(m) for r in ok])) is not None
        },
        audio=_avg([r["audio"] for r in ok]),
        vmaf=_avg([r["vmaf"] for r in ok]),
        change_db=_avg([r["audio_change_db"] for r in ok]),
        over_cap=any(s > BY_NAME[k].cap + 1e-9 for k, s in strengths.items()),
    )


def _pick_video(trials: list[TrialRow], base_vmaf: float) -> dict[str, TrialRow]:
    pool = [
        t
        for t in trials
        if t.part is Part.VIDEO and not t.errors and t.vmaf and t.phase != "baseline"
    ]
    picks = {}
    for name, drop in _VIDEO_PICKS.items():
        floor = base_vmaf - drop
        fit = [
            t
            for t in pool
            if t.vmaf and t.vmaf >= floor and (name == "wild" or not t.over_cap)
        ]
        if fit:
            picks[name] = min(
                fit,
                key=lambda t: (
                    mean(t.methods.values()) if t.methods else 100.0,
                    t.caught,
                    -(t.vmaf or 0),
                ),
            )
    return picks


def _pick_audio(trials: list[TrialRow], base_db: float) -> dict[str, TrialRow]:
    pool = [
        t
        for t in trials
        if t.part is Part.AUDIO
        and not t.errors
        and t.change_db is not None
        and t.phase != "baseline"
    ]
    picks = {}
    for name, extra in _AUDIO_PICKS.items():
        fit = [
            t
            for t in pool
            if (t.change_db or 0) <= base_db + extra
            and (name == "wild" or not t.over_cap)
        ]
        if fit:
            picks[name] = min(fit, key=lambda t: (t.audio or 0, t.change_db or 0))
    return picks


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _flat(t: TrialRow) -> dict[str, Any]:
    return {
        "trial": t.trial,
        "phase": t.phase,
        "part": t.part.value,
        "caught_%": t.caught,
        "methods_beaten": sum(
            1 for m in _METHODS if m in t.methods and t.methods[m] < CAUGHT_PERCENT
        ),
        "mean_found_%": round(mean(t.methods.values()), 1) if t.methods else None,
        **{f"{m}_%": v for m, v in t.methods.items()},
        "audio_%": t.audio,
        "vmaf": t.vmaf,
        "change_db": t.change_db,
        "over_cap": t.over_cap,
        "knobs": " ".join(f"{k}={v:g}" for k, v in sorted(t.strengths.items())),
    }


def _validate(job: tuple[str, dict[str, float], str, int, str]) -> Measurement:
    name, strengths, clip, seed, keep = job
    return measure(
        name,
        strengths,
        Part.VIDEO,
        Path(clip),
        seed,
        full=True,
        keep=Path(keep) if keep else None,
    )


def _side_by_side(source: Path, copy: Path, dst: Path) -> None:
    subprocess.run(
        [
            binary("ffmpeg"),
            "-y",
            "-v",
            "error",
            "-i",
            str(source),
            "-i",
            str(copy),
            "-filter_complex",
            (
                "[0:v]setpts=PTS-STARTPTS,scale=-2:960[s];"
                "[1:v]setpts=PTS-STARTPTS,scale=-2:960[c];"
                "[s][c]hstack=shortest=1,format=yuv420p"
            ),
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-crf",
            "24",
            "-preset",
            "fast",
            "-c:a",
            "aac",
            "-shortest",
            str(dst),
        ],
        check=True,
        capture_output=True,
        timeout=900,
    )


def _table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p>none</p>"
    head = "".join(f"<th>{html.escape(k)}</th>" for k in rows[0])
    body = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape('' if v is None else str(v))}</td>" for v in r.values()
        )
        + "</tr>"
        for r in rows
    )
    return f"<table><tr>{head}</tr>{body}</table>"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Lab report")
    parser.add_argument("clips", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args(argv)
    clips = sorted(args.clips.glob("*.mp4"))
    report = args.out / "report"
    (report / "presets").mkdir(parents=True, exist_ok=True)
    (report / "examples").mkdir(parents=True, exist_ok=True)
    trials = [
        _trial(name, rows) for name, rows in _load(args.out / "results.jsonl").items()
    ]
    base_db = next((t.change_db for t in trials if t.trial == "base_audio"), 0.0) or 0.0

    ablation = sorted(
        (t for t in trials if t.phase in ("baseline", "ablation")),
        key=lambda t: (t.part.value, t.trial),
    )
    _write_csv(report / "knobs.csv", [_flat(t) for t in ablation])
    explored = sorted(
        (t for t in trials if t.phase in ("random", "search")),
        key=lambda t: (
            t.part.value,
            t.caught if t.part is Part.VIDEO else (t.audio or 0),
        ),
    )
    _write_csv(report / "trials.csv", [_flat(t) for t in explored])

    base_vmaf = next((t.vmaf for t in trials if t.trial == "base_video"), None) or 95.0
    video, audio = _pick_video(trials, base_vmaf), _pick_audio(trials, base_db)
    presets: dict[str, dict[str, float]] = {}
    for name in _VIDEO_PICKS:
        v, a = video.get(name), audio.get(name)
        strengths = {**(v.strengths if v else {}), **(a.strengths if a else {})}
        presets[name] = strengths
        made = preset(
            strengths,
            label=f"stealth {name}: lab pick (video {v.trial if v else '-'}, audio {a.trial if a else '-'})",
        )
        body = made.model_dump(mode="json", exclude_unset=True)
        body["device"] = "strip"
        (report / "presets" / f"stealth_{name}.json").write_text(
            json.dumps(body, indent=2), encoding="utf-8"
        )

    jobs = [
        (
            f"val_{name}",
            strengths,
            str(clip),
            seed,
            str(report / "examples" / f"_copy_{name}_{clip.stem}.mp4")
            if seed == _VALIDATION_SEEDS[0] and i < _EXAMPLE_CLIPS
            else "",
        )
        for name, strengths in presets.items()
        for i, clip in enumerate(clips)
        for seed in _VALIDATION_SEEDS
    ]
    with ProcessPoolExecutor(args.workers) as pool:
        validated = list(pool.map(_validate, jobs))
    rows = [
        {
            "preset": m.trial.removeprefix("val_"),
            "clip": m.sample,
            "seed": m.seed,
            "caught_%": m.video_caught,
            **{f"{k}_%": v for k, v in m.video.items()},
            "audio_%": m.audio,
            "vmaf": m.vmaf,
            "change_db": m.audio_change_db,
            "error": m.error,
        }
        for m in validated
    ]
    _write_csv(report / "validation.csv", rows)
    summary = []
    for name in presets:
        ms = [m for m in validated if m.trial == f"val_{name}" and not m.error]
        summary.append(
            {
                "preset": f"stealth_{name}",
                "copies": len(ms),
                "picture caught (copies)": sum(
                    m.video_caught >= CAUGHT_PERCENT for m in ms
                ),
                "sound caught (copies)": sum(
                    (m.audio or 0) >= CAUGHT_PERCENT for m in ms
                ),
                "picture found % (mean)": _avg([m.video_caught for m in ms]),
                "sound found % (mean)": _avg([m.audio for m in ms]),
                "VMAF (mean)": _avg([m.vmaf for m in ms]),
                "VMAF (min)": min(
                    (m.vmaf for m in ms if m.vmaf is not None), default=None
                ),
                "sound change dB (mean)": _avg([m.audio_change_db for m in ms]),
            }
        )
    examples = []
    for name in presets:
        for clip in clips[:_EXAMPLE_CLIPS]:
            copy = report / "examples" / f"_copy_{name}_{clip.stem}.mp4"
            if copy.exists():
                dst = report / "examples" / f"{name}_{clip.stem}.mp4"
                _side_by_side(clip, copy, dst)
                copy.unlink()
                examples.append((name, clip.stem, dst.name))

    knob_rows = [_flat(t) for t in ablation]
    pick_rows = [
        {"pick": f"{part} {name}", **_flat(t)}
        for part, picks in (("video", video), ("audio", audio))
        for name, t in picks.items()
    ]
    videos = "".join(
        f"<figure><video controls preload='metadata' src='examples/{html.escape(f)}'></video>"
        f"<figcaption>stealth_{html.escape(n)} · {html.escape(c)} (left source, right copy)</figcaption></figure>"
        for n, c, f in examples
    )
    page = f"""<!doctype html><meta charset=utf-8><title>Repurpose lab</title>
<style>body{{font:14px system-ui;margin:24px;max-width:1400px}}table{{border-collapse:collapse;margin:8px 0 24px;font-size:12px}}
td,th{{border:1px solid #ccc;padding:3px 6px;text-align:left}}th{{background:#f3f3f3}}figure{{display:inline-block;margin:0 12px 12px 0}}
video{{height:480px}}</style>
<h1>Repurpose lab</h1>
<p>% = share of the copy a method finds in the source (50+ = caught). VMAF: picture damage vs a reference with the same framing (100 = identical; an untouched copy scores {base_vmaf}: the 720p encode alone; sharpening can push VMAF above it). Sound change: dB from the source (untouched copy: {base_db} dB).</p>
<h2>Presets (validated on {len(clips)} clips x {len(_VALIDATION_SEEDS)} seeds)</h2>{_table(summary)}
<p>Files: presets/stealth_*.json</p>
<h2>Examples</h2>{videos}
<h2>Picks</h2>{_table(pick_rows)}
<h2>Each knob alone</h2>{_table(knob_rows)}
<h2>Best 60 explored (random throws + search)</h2>{_table([_flat(t) for t in explored if t.part is Part.VIDEO][:40] + [_flat(t) for t in explored if t.part is Part.AUDIO][:20])}
"""
    (report / "index.html").write_text(page, encoding="utf-8")
    print(f"report: {report / 'index.html'}", flush=True)


if __name__ == "__main__":
    main()
