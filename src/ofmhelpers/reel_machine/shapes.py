"""
Reel "shapes" -- proven voice-count/camera-holder/pacing patterns for a
Seedance prompt package. Ported from the old reel-machine skill bundle's
shapes.md reference. A shape only fixes the structure; the actual dialogue
comes from the teardown (see teardown.py).

Text fields use {label}/{noun}/{noun_cap}/{subject}/{object}/{possessive}/
{tag} placeholders instead of hardcoded "female"/"she"/"her" -- render_shape()
fills them in for whichever gender the user picked on the /replicate form
(prompt_builder.build_prompt_package calls this). This is pronoun/voice-
register correctness only, never physical appearance -- identity always
comes from the reference images (see prompt_builder's RULE block).
"""

from dataclasses import dataclass

from ofmhelpers.reel_machine.gender import Gender, render_text


@dataclass(frozen=True)
class Shape:
    name: str
    voices: str
    camera: str
    pacing: str
    notes: str


SHAPES: dict[str, Shape] = {
    "solo_monologue": Shape(
        name="Solo UGC monologue",
        voices="1 {label}, on-cam, lip-sync",
        camera="selfie -- {subject} holds the phone",
        pacing=(
            "story pace: connected sentences, small beats between thoughts, one longer "
            "pause before the punchline"
        ),
        notes="dies if it drags -- keep the words tight for the duration",
    ),
    "duet_selfie": Shape(
        name="Duet -- selfie",
        voices="{object} (on-cam, {label}, lip-sync) + an off-cam voice, never shown",
        camera="{subject} holds the phone",
        pacing="slow/sparse -- roughly half the clip is intentional silence",
        notes="the silence IS the tension; each line lands then a beat of quiet",
    ),
    "duet_pov": Shape(
        name="Duet -- POV",
        voices=(
            "{object} (on-cam, {label}, lip-sync, main speaker) + an off-cam voice for a "
            "mid-clip reaction beat"
        ),
        camera="the other person holds the phone POV-style -- {possessive} hands are free",
        pacing="slow/sparse + a quiet beat where {subject} goes silent while the other voice reacts",
        notes="pairs well with the gopro_pov look",
    ),
    "woman_x_woman": Shape(
        name="{noun_cap} x {noun}",
        voices=(
            "2 distinct {label} voices -- one on-cam (from the reference images), one "
            "off-cam or edge-of-frame"
        ),
        camera="selfie or POV",
        pacing="banter: quick short exchanges with small pauses, one longer pause before the twist",
        notes="tagging matters most here -- two same-gender voices collapse without distinct registers",
    ),
    "cta_talking_head": Shape(
        name="CTA talking-head",
        voices="1 {label}, on-cam, lip-sync",
        camera="selfie",
        pacing="ONE flowing breath-group, connected lines, no gaps",
        notes="the opposite of the duets -- this shape dies WITH pauses, not without them",
    ),
    "action_no_dialogue": Shape(
        name="Action / no dialogue",
        voices="none -- ambient audio only",
        camera="POV or third-person",
        pacing="pure motion beats, still timed per-second",
        notes="max one positional change; fast continuous motion breaks the face",
    ),
}


def render_shape(shape: Shape, gender: Gender) -> Shape:
    """Resolves a shape's gender placeholders for a specific Gender --
    called by prompt_builder.build_prompt_package before interpolating the
    shape into the final package text."""
    return Shape(
        name=render_text(shape.name, gender),
        voices=render_text(shape.voices, gender),
        camera=render_text(shape.camera, gender),
        pacing=render_text(shape.pacing, gender),
        notes=render_text(shape.notes, gender),
    )
