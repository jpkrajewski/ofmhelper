"""
End-to-end orchestration for the /replicate web route: run intake, then
draft a Seedance prompt package from it. Kept separate from intake.py /
prompt_builder.py so the web layer only needs these two entry points.
"""

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


def intake_reel(url_or_path: str, work_dir: Path) -> IntakeResult:
    return run_intake(url_or_path, work_dir)


def draft_script(
    intake: IntakeResult,
    shape_key: str = DEFAULT_SHAPE,
    look_key: str = DEFAULT_LOOK,
    duration: int = 15,
    llm_provider: str | None = None,
    target: str = "",
    gender: str = DEFAULT_GENDER,
) -> str:
    """`target` is a persona/tone brief for the character being built (e.g.
    "confident fitness coach pitching a program") and `gender` picks the
    pronouns/voice-register the shape templates render with (see gender.py)
    -- neither ever describes physical appearance; identity always comes
    from the reference images (prompt_builder's RULE block)."""
    shape = SHAPES.get(shape_key, SHAPES[DEFAULT_SHAPE])
    look = LOOKS.get(look_key, LOOKS[DEFAULT_LOOK])
    teardown = build_teardown_draft(intake.transcript, duration=duration)

    provider = get_provider(llm_provider)

    # VISION step -- actually looks at the contact sheet to fill in the hook/
    # viral_mechanic/camera_look that build_teardown_draft can only leave as
    # "(edit me)" placeholders (it only has the transcript, not the frames).
    # TemplateProvider's analyze_reel is a no-op (no vision, no cost); a
    # failed/misconfigured vision-capable provider just keeps the
    # placeholders instead of failing the job.
    try:
        analysis = provider.analyze_reel(intake.contact_sheet, intake.transcript.text)
    except Exception as exc:
        print(
            f"[reel_machine] {provider.name} analyze_reel failed ({exc}), "
            "keeping the (edit me) placeholders",
            flush=True,
        )
        analysis = {}
    if analysis.get("hook"):
        teardown.hook = analysis["hook"]
    if analysis.get("viral_mechanic"):
        teardown.viral_mechanic = analysis["viral_mechanic"]
    if analysis.get("camera_look"):
        teardown.camera_look = analysis["camera_look"]

    try:
        return provider.write_prompt_package(
            teardown, shape, look, duration, target=target, gender=gender
        )
    except Exception as exc:
        print(
            f"[reel_machine] {provider.name} provider failed ({exc}), falling back to template",
            flush=True,
        )
        return TemplateProvider().write_prompt_package(
            teardown, shape, look, duration, target=target, gender=gender
        )
