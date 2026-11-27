# Copyright (C) 2026 jpkrajewski

from pathlib import Path

import pytest

from ofmhelpers.config import settings
from ofmhelpers.media.repurpose.checker.schema import AudioScore, CheckReport
from ofmhelpers.media.repurpose.pipelines.models import (
    AudioRetry,
    PipelineSpec,
    PlannedTry,
    Stage,
    Verdict,
)
from ofmhelpers.media.repurpose.pipelines.pipeline import Pipeline
from ofmhelpers.media.repurpose.pipelines.planner import ExecutionPlanner
from ofmhelpers.media.repurpose.presets import load_preset
from ofmhelpers.media.repurpose.repurpose import Repurpose
from ofmhelpers.media.repurpose.transformations.audio import (
    AudioSettings,
    AudioTransformer,
)


def _spoof() -> PipelineSpec:
    return Pipeline.paths("source.mp4", "copy.mov").load("spoof").spec


def _old_15_try_spec() -> PipelineSpec:
    return PipelineSpec(
        stages=[
            Stage(preset="spoof", max_attempts=3, tries_per_attempt=3),
            Stage(preset="spoof_fallback", max_attempts=2, tries_per_attempt=2),
            Stage(preset="hard", max_attempts=1, tries_per_attempt=2),
        ],
    )


def _report(*, looks: bool, sounds: bool) -> CheckReport:
    return CheckReport(
        source_file=Path("source.mp4"),
        copy_file=Path("copy.mov"),
        source_frames=80,
        copy_frames=8,
        flat_copy_frames=0,
        hashes=[],
        audio=AudioScore(
            similarity=0.8 if sounds else 0.6,
            offset_s=0.0,
            threshold=0.7,
            matched_share=1.0 if sounds else 0.0,
        ),
        looks_like_source=looks,
        sounds_like_source=sounds,
    )


def test_the_spoof_pipeline_counts_every_ffmpeg_run_and_stays_in_the_cap():
    spec = _spoof()
    planner = ExecutionPlanner(spec, settings.repurpose.max_tries)

    picture_tries: list[PlannedTry] = list(planner.picture_tries())
    audio_retries: list[AudioRetry] = list(planner.audio_retries())

    assert len(picture_tries) == spec.picture_executions == 7
    assert len(audio_retries) == spec.audio_executions == 3
    assert planner.planned_executions == 10 <= settings.repurpose.max_tries
    planner.refuse_over_the_cap()


def test_the_spoof_pipeline_keeps_the_first_sound_and_other_pipelines_do_not():
    assert _spoof().keep_first_sound
    assert not PipelineSpec().keep_first_sound


def test_the_old_15_try_spoof_plan_is_refused():
    planner = ExecutionPlanner(_old_15_try_spec(), settings.repurpose.max_tries)

    assert len(list(planner.picture_tries())) == 15
    with pytest.raises(ValueError, match="this one up to 15"):
        planner.refuse_over_the_cap()


def test_a_plan_over_the_cap_is_refused_before_the_first_encode(tmp_path):
    output = tmp_path / "IMG_1.mov"
    pipeline = Pipeline.paths(tmp_path / "missing.mp4", output).stage(
        "spoof",
        max_attempts=settings.repurpose.max_tries + 1,
    )

    with pytest.raises(ValueError, match="OFM_REPURPOSE_MAX_TRIES"):
        pipeline.run()

    assert not output.exists()


def test_audio_retries_count_toward_the_cap():
    spec = PipelineSpec(
        stages=[Stage(preset="spoof", max_attempts=settings.repurpose.max_tries)],
        audio_retries=_spoof().audio_retries[:1],
    )

    with pytest.raises(ValueError, match="1 audio retries"):
        ExecutionPlanner(spec, settings.repurpose.max_tries).refuse_over_the_cap()


def test_audio_retries_on_a_device_that_records_its_copy_are_refused():
    spec = PipelineSpec(
        device="iphone16_usa",
        stages=[Stage(preset="spoof")],
        audio_retries=_spoof().audio_retries[:1],
    )
    planner = ExecutionPlanner(spec, settings.repurpose.max_tries)

    with pytest.raises(ValueError, match="records its copy"):
        planner.refuse_audio_retries_on_a_recorder()


def test_the_spoof_pipeline_and_one_without_audio_retries_are_not_refused():
    recorded = PipelineSpec(device="iphone16_usa", stages=[Stage(preset="spoof")])

    ExecutionPlanner(_spoof(), 10).refuse_audio_retries_on_a_recorder()
    ExecutionPlanner(recorded, 10).refuse_audio_retries_on_a_recorder()


def test_the_budget_ends_at_the_cap_whatever_kind_ran():
    planner = ExecutionPlanner(_spoof(), 10)

    assert planner.budget_left(9)
    assert not planner.budget_left(10)


def test_a_verdict_tells_a_picture_failure_from_a_sound_failure():
    picture = Verdict(report=_report(looks=True, sounds=False))
    sound = Verdict(report=_report(looks=False, sounds=True))
    broken = Verdict(
        report=_report(looks=False, sounds=False),
        broken_audio="no audio stream",
    )
    unique = Verdict(report=_report(looks=False, sounds=False))

    assert (picture.picture_passed, picture.audio_passed) == (False, True)
    assert (sound.picture_passed, sound.audio_passed) == (True, False)
    assert (broken.picture_passed, broken.audio_passed) == (True, False)
    assert not picture.passed
    assert not sound.passed
    assert not broken.passed
    assert unique.passed


def test_every_audio_retry_cuts_more_low_end_than_the_one_before():
    cuts: list[tuple[float, float]] = []
    for retry in _spoof().audio_retries:
        assert isinstance(retry.audio.highpass_hz, tuple)
        cuts.append(retry.audio.highpass_hz)

    assert len(cuts) == 3
    assert cuts == sorted(cuts)
    assert load_preset("spoof").audio.highpass_hz is None


def test_the_low_cut_knob_becomes_a_highpass_filter_after_the_pitch():
    transformer = AudioTransformer(
        AudioSettings(pitch=0.75, highpass_hz=120.0),
        1.0,
        48000,
        Repurpose("source.mp4", "copy.mov", seed=1).seeded_random(),
    )

    chain = transformer.filters().split(",")

    assert "highpass=f=120" in chain
    assert chain.index("highpass=f=120") > chain.index("asetrate=50125")
    assert transformer.draws.highpass_hz == 120.0
