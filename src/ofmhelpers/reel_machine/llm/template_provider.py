"""
The default, always-available provider: no API call, no cost, fully
deterministic. Wraps prompt_builder.build_prompt_package directly. Has no
vision -- analyze_reel is a no-op, so the (edit me) hook/viral_mechanic/
camera_look placeholders from teardown.build_teardown_draft stay in place
for the user to fill in by hand.
"""

from pathlib import Path

from ofmhelpers.reel_machine.gender import DEFAULT_GENDER
from ofmhelpers.reel_machine.looks import Look
from ofmhelpers.reel_machine.prompt_builder import build_prompt_package
from ofmhelpers.reel_machine.shapes import Shape
from ofmhelpers.reel_machine.teardown import Teardown


class TemplateProvider:
    name = "template"

    def analyze_reel(
        self,
        contact_sheet: Path,
        transcript_text: str,
        video_path: Path | None = None,
    ) -> dict:
        return {}

    def write_prompt_package(
        self,
        teardown: Teardown,
        shape: Shape,
        look: Look,
        duration: int,
        target: str = "",
        gender: str = DEFAULT_GENDER,
    ) -> str:
        return build_prompt_package(
            teardown, shape, look, duration, target=target, gender=gender
        )
