"""The knob search, a CLI:

    python -m ofmhelpers.repurpose.lab.search CLIPS_DIR OUT_DIR --minutes 30 --workers 16

Every measurement (`measure.Measurement`) is appended to OUT/results.jsonl
the moment it lands, so a cut-off run keeps what it measured. Phases, each
with its share of the time:

1. baseline + ablation (to 35%): no knobs, then each knob alone at 1/3, 2/3
   and all of its cap, and past it (`space.OVER_CAP`), on the same 2 clips;
2. random throws (to 60%): random knob mixes, each knob on at p 0.35 with a
   strength up to OVER_CAP x its cap, on 2 random clips;
3. search (to the end): Optuna NSGA-II per part within the caps, on 3
   clips: video lowers the caught % at VMAF >= `VMAF_FLOOR` and raises
   VMAF; audio lowers Chromaprint's % and the sound change.
"""

import argparse
import hashlib
import json
import random
import time
from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from pathlib import Path
from statistics import mean
from typing import Any

import optuna
from pydantic import BaseModel

from ofmhelpers.repurpose.lab.calibrate import Calibration, cross_pairs
from ofmhelpers.repurpose.lab.measure import Measurement, measure
from ofmhelpers.repurpose.lab.space import BY_NAME, OVER_CAP, Part, knobs

# The search's VMAF floor under an untouched copy's VMAF (the device's own
# encode already costs ~5: 94.9 on the strip device, 2026-10-26), and the
# score never counts above that baseline (sharpening raises VMAF: no reward).
VMAF_DROP = 3.0
_DEFAULT_BASELINE = 95.0
# Best earlier throws per part that start the NSGA-II population.
_SEEDS = {"video": 16, "audio": 8}
_ABLATION_STEPS = (1 / 3, 2 / 3, 1.0, OVER_CAP)
_RANDOM_ON = 0.35
_PHASE_ENDS = {"ablation": 0.35, "random": 0.60, "search": 1.0}


class Job(BaseModel):
    trial: str
    phase: str
    part: Part
    strengths: dict[str, float]
    sample: str
    seed: int
    # Optuna: (part, trial number) this measurement belongs to.
    study_trial: int | None = None


def _seed(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:8], 16)


def _jobs(
    trial: str,
    phase: str,
    part: Part,
    strengths: dict[str, float],
    clips: list[Path],
    study_trial: int | None = None,
) -> list[Job]:
    return [
        Job(
            trial=trial,
            phase=phase,
            part=part,
            strengths=strengths,
            sample=str(c),
            seed=_seed(trial, c.name),
            study_trial=study_trial,
        )
        for c in clips
    ]


def ablation(clips: list[Path]) -> Iterator[Job]:
    pair = clips[:2]
    for part in Part:
        yield from _jobs(f"base_{part.value}", "baseline", part, {}, pair)
    for part in Part:
        for knob in knobs(part):
            for step in _ABLATION_STEPS:
                s = round(knob.cap * step, 5)
                yield from _jobs(
                    f"abl_{knob.name}_{step:.2f}",
                    "ablation",
                    part,
                    {knob.name: s},
                    pair,
                )


def throws(clips: list[Path], rng: random.Random) -> Iterator[Job]:
    n = 0
    while True:
        n += 1
        part = Part.AUDIO if n % 3 == 0 else Part.VIDEO
        strengths = {
            k.name: round(k.cap * rng.uniform(0.2, OVER_CAP), 5)
            for k in knobs(part)
            if rng.random() < _RANDOM_ON
        }
        yield from _jobs(f"rnd_{n}", "random", part, strengths, rng.sample(clips, 2))


class Searcher:
    """Two NSGA-II studies (video, audio), asked and told around the pool."""

    def __init__(self, clips: list[Path], seed: int, baseline: float) -> None:
        self.clips = clips
        self.baseline = baseline
        self.rng = random.Random(seed)  # noqa: S311 - sampling, not cryptography
        self.studies = {
            Part.VIDEO: optuna.create_study(
                directions=["minimize", "maximize"],
                sampler=optuna.samplers.NSGAIISampler(seed=seed),
            ),
            Part.AUDIO: optuna.create_study(
                directions=["minimize", "minimize"],
                sampler=optuna.samplers.NSGAIISampler(seed=seed + 1),
            ),
        }
        self.open: dict[
            tuple[Part, int], tuple[optuna.Trial, list[Measurement], int]
        ] = {}

    def jobs(self) -> Iterator[Job]:
        n = 0
        while True:
            n += 1
            part = Part.AUDIO if n % 3 == 0 else Part.VIDEO
            trial = self.studies[part].ask()
            strengths = {}
            for k in knobs(part):
                on = trial.suggest_categorical(f"{k.name}_on", [False, True])
                s = trial.suggest_float(k.name, 0.15 * k.cap, k.cap)
                if on:
                    strengths[k.name] = round(s, 5)
            picked = self.rng.sample(self.clips, min(3, len(self.clips)))
            self.open[(part, trial.number)] = (trial, [], len(picked))
            yield from _jobs(
                f"opt_{part.value}_{trial.number}",
                "search",
                part,
                strengths,
                picked,
                trial.number,
            )

    def done(self, job: Job, result: Measurement) -> None:
        key = (job.part, job.study_trial or 0)
        if job.study_trial is None or key not in self.open:
            return
        trial, got, want = self.open[key]
        got.append(result)
        if len(got) < want:
            return
        del self.open[key]
        study = self.studies[job.part]
        if any(m.error for m in got):
            study.tell(trial, state=optuna.trial.TrialState.FAIL)
            return
        if job.part is Part.VIDEO:
            vmaf = mean(m.vmaf or 0 for m in got)
            trial.set_constraint("vmaf_floor", self.baseline - VMAF_DROP - vmaf)
            study.tell(trial, [mean(_found(m) for m in got), min(vmaf, self.baseline)])
        else:
            study.tell(
                trial,
                [
                    mean(m.audio or 0 for m in got),
                    mean(m.audio_change_db or 0 for m in got),
                ],
            )

    def seed(self, results: Path) -> int:
        """Start each study from the best earlier random throws within the
        caps (video: the least caught at the floor; audio: the least found)."""
        rows: dict[str, list[dict[str, Any]]] = {}
        for line in results.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row["phase"] == "random" and not row["error"]:
                rows.setdefault(row["trial"], []).append(row)
        ranked: dict[Part, list[tuple[float, dict[str, float]]]] = {p: [] for p in Part}
        for found in rows.values():
            strengths = found[0]["strengths"]
            if any(s > BY_NAME[k].cap for k, s in strengths.items()):
                continue
            part = Part(found[0]["part"])
            if part is Part.VIDEO:
                if mean(r["vmaf"] or 0 for r in found) < self.baseline - VMAF_DROP:
                    continue
                score = mean(mean(r["video"].values() or [0]) for r in found)
            else:
                score = mean(r["audio"] or 0 for r in found)
            ranked[part].append((score, strengths))
        count = 0
        for part, best in ranked.items():
            for _, strengths in sorted(best, key=lambda b: b[0])[: _SEEDS[part.value]]:
                params: dict[str, Any] = {}
                for k in knobs(part):
                    on = k.name in strengths
                    params[f"{k.name}_on"] = on
                    params[k.name] = min(
                        max(strengths.get(k.name, 0.5 * k.cap), 0.15 * k.cap), k.cap
                    )
                self.studies[part].enqueue_trial(params)
                count += 1
        return count


def _found(m: Measurement) -> float:
    """What the video search lowers: the mean % found over every method
    (SSCD holds ~92% on every in-cap mix, so the worst method alone gave the
    search nothing to climb; 2026-10-26)."""
    return mean(m.video.values()) if m.video else 100.0


def baseline_vmaf(results: Path) -> float:
    """An untouched copy's VMAF, from this run's baseline measurements."""
    if not results.exists():
        return _DEFAULT_BASELINE
    got = [
        row["vmaf"]
        for line in results.read_text(encoding="utf-8").splitlines()
        if (row := json.loads(line))["trial"] == "base_video" and row["vmaf"]
    ]
    return round(mean(got), 2) if got else _DEFAULT_BASELINE


class Runner:
    def __init__(self, workers: int, results: Path) -> None:
        self.pool = ProcessPoolExecutor(workers)
        self.room = workers + 4
        self.results = results
        self.inflight: dict[Future[Measurement], Job] = {}
        self.count = 0

    def submit(self, job: Job) -> None:
        future = self.pool.submit(
            measure, job.trial, job.strengths, job.part, Path(job.sample), job.seed
        )
        self.inflight[future] = job

    def collect(self, on_done: Callable[[Job, Measurement], None], block: bool) -> None:
        if not self.inflight:
            return
        finished, _ = wait(
            self.inflight, timeout=None if block else 0, return_when=FIRST_COMPLETED
        )
        with self.results.open("a", encoding="utf-8") as out:
            for future in finished:
                job = self.inflight.pop(future)
                result = future.result()
                row = {
                    **result.model_dump(mode="json"),
                    "phase": job.phase,
                    "strengths": job.strengths,
                }
                out.write(json.dumps(row) + "\n")
                self.count += 1
                on_done(job, result)

    def phase(
        self,
        jobs: Iterator[Job],
        until: float,
        on_done: Callable[[Job, Measurement], None],
    ) -> None:
        for job in jobs:
            while len(self.inflight) >= self.room:
                self.collect(on_done, block=True)
            if time.monotonic() >= until:
                return
            self.submit(job)

    def finish(self, on_done: Callable[[Job, Measurement], None]) -> None:
        while self.inflight:
            self.collect(on_done, block=True)
        self.pool.shutdown()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Repurpose knob search")
    parser.add_argument("clips", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--calibrate-only", action="store_true", help="only the cross-pair check"
    )
    parser.add_argument(
        "--phases", default="ablation,random,search", help="comma list, in order"
    )
    args = parser.parse_args(argv)
    clips = sorted(args.clips.glob("*.mp4"))
    args.out.mkdir(parents=True, exist_ok=True)
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    saved = args.out / "calibration.json"
    checked = (
        Calibration.model_validate_json(saved.read_text(encoding="utf-8"))
        if saved.exists()
        else cross_pairs(clips, args.out)
    )
    print(
        f"cross pairs: {len(checked.pairs)}, SSCD flagged {checked.sscd_flagged}, "
        f"TMK flagged {checked.tmk_flagged}",
        flush=True,
    )
    if args.calibrate_only:
        return
    started = time.monotonic()
    budget = args.minutes * 60
    results = args.out / "results.jsonl"
    runner = Runner(args.workers, results)
    baseline = baseline_vmaf(results)
    searcher = Searcher(clips, args.seed, baseline)
    wanted = args.phases.split(",")
    if "random" not in wanted and results.exists():
        print(f"seeded {searcher.seed(results)} trials", flush=True)
    print(f"VMAF baseline {baseline}, floor {baseline - VMAF_DROP}", flush=True)

    def on_done(job: Job, result: Measurement) -> None:
        searcher.done(job, result)
        if runner.count % 25 == 0:
            print(
                f"{time.monotonic() - started:6.0f}s  {runner.count} measured  ({job.phase})",
                flush=True,
            )

    phases = {
        "ablation": ablation(clips),
        "random": throws(clips, random.Random(args.seed)),  # noqa: S311 - sampling
        "search": searcher.jobs(),
    }
    for name, jobs in phases.items():
        if name not in wanted:
            continue
        print(f"phase {name}", flush=True)
        runner.phase(jobs, started + budget * _PHASE_ENDS[name], on_done)
    runner.finish(on_done)
    print(
        f"done: {runner.count} measurements in {time.monotonic() - started:.0f}s",
        flush=True,
    )


if __name__ == "__main__":
    main()
