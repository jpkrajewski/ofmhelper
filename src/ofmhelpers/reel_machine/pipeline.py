"""
End-to-end orchestration for the /replicate web route: run intake, then
draft a Seedance prompt package from it. Kept separate from intake.py /
prompt_builder.py so the web layer only needs these two entry points.
"""

from dataclasses import dataclass
from pathlib import Path

from ofmhelpers.reel_machine.gender import DEFAULT_GENDER
from ofmhelpers.reel_machine.intake import IntakeResult, run_intake
from ofmhelpers.reel_machine.llm.registry import get_provider
from ofmhelpers.reel_machine.llm.template_provider import TemplateProvider
from ofmhelpers.reel_machine.looks import LOOKS
from ofmhelpers.reel_machine.shapes import SHAPES
from ofmhelpers.reel_machine.teardown import build_teardown_draft

DEFAULT_SHAPE = "solo_monologue"
DEFAULT_LOOK = "phone_selfie"

# Seedance 2.0's supported duration range (see kaiai/client.py's
# generate_video_seedance2 / the /replicate form's min/max) -- the source
# reel's own duration is clamped into this range rather than exposed as a
# manual field the user has to set.
MIN_DURATION_S = 4
MAX_DURATION_S = 15


def intake_reel(url_or_path: str, work_dir: Path) -> IntakeResult:
    return run_intake(url_or_path, work_dir)


def clamp_duration(seconds: float) -> int:
    return max(MIN_DURATION_S, min(MAX_DURATION_S, round(seconds)))


@dataclass
class DraftResult:
    script: str
    # The vision step's main-subject identification -- an internal
    # targeting aid surfaced to the user as read-only review context (see
    # web/templates/replicate_review.html), never merged into `script`
    # itself (identity in the generated PROMPT is reference-images only).
    main_subject: str = ""


def draft_script(
    intake: IntakeResult,
    shape_key: str = DEFAULT_SHAPE,
    look_key: str = DEFAULT_LOOK,
    duration: int | None = None,
    llm_provider: str | None = None,
    target: str = "",
    gender: str = DEFAULT_GENDER,
) -> str:
    """Back-compat wrapper around draft_script_full() for callers that only
    want the script text. See draft_script_full's docstring."""
    return draft_script_full(
        intake,
        shape_key=shape_key,
        look_key=look_key,
        duration=duration,
        llm_provider=llm_provider,
        target=target,
        gender=gender,
    ).script


def draft_script_full(
    intake: IntakeResult,
    shape_key: str = DEFAULT_SHAPE,
    look_key: str = DEFAULT_LOOK,
    duration: int | None = None,
    llm_provider: str | None = None,
    target: str = "",
    gender: str = DEFAULT_GENDER,
) -> DraftResult:
    """`target` is a persona/tone brief for the character being built (e.g.
    "confident fitness coach pitching a program") and `gender` picks the
    pronouns/voice-register the shape templates render with (see gender.py)
    -- neither ever describes physical appearance; identity always comes
    from the reference images (prompt_builder's RULE block). `duration`
    defaults to the source reel's own duration (intake.duration, clamped to
    Seedance's supported range) -- the generated clone should always match
    the original's length, not a manually picked value."""
    shape = SHAPES.get(shape_key, SHAPES[DEFAULT_SHAPE])
    look = LOOKS.get(look_key, LOOKS[DEFAULT_LOOK])
    if duration is None:
        duration = clamp_duration(intake.duration)
    teardown = build_teardown_draft(intake.transcript, duration=duration)

    provider = get_provider(llm_provider)

    # VISION step -- looks at the full video (falls back to the contact
    # sheet -- see gemini_provider.analyze_reel) to fill in the hook/
    # viral_mechanic/camera_look that build_teardown_draft can only leave as
    # "(edit me)" placeholders (it only has the transcript, not the frames),
    # plus main_subject -- an internal targeting aid so the analysis tracks
    # the right person when a reel has more than one in frame (never merged
    # into the generated PROMPT text -- identity there is reference-images
    # only, per prompt_builder's RULE block).
    # TemplateProvider's analyze_reel is a no-op (no vision, no cost); a
    # failed/misconfigured vision-capable provider just keeps the
    # placeholders instead of failing the job.
    try:
        analysis = provider.analyze_reel(
            intake.contact_sheet, intake.transcript.text, video_path=intake.video_path
        )
    except Exception as exc:
        print(
            f"[reel_machine] {provider.name} analyze_reel failed ({exc}), "
            "keeping the (edit me) placeholders",
            flush=True,
        )
        analysis = {}
    if analysis.get("main_subject"):
        teardown.main_subject = analysis["main_subject"]
    if analysis.get("subject_action"):
        teardown.subject_action = analysis["subject_action"]
    if analysis.get("hook"):
        teardown.hook = analysis["hook"]
    if analysis.get("viral_mechanic"):
        teardown.viral_mechanic = analysis["viral_mechanic"]
    if analysis.get("camera_look"):
        teardown.camera_look = analysis["camera_look"]

    try:
        script = provider.write_prompt_package(
            teardown, shape, look, duration, target=target, gender=gender
        )
    except Exception as exc:
        print(
            f"[reel_machine] {provider.name} provider failed ({exc}), falling back to template",
            flush=True,
        )
        script = TemplateProvider().write_prompt_package(
            teardown, shape, look, duration, target=target, gender=gender
        )
    return DraftResult(script=script, main_subject=teardown.main_subject)
