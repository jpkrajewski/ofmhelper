"""
Builds the Seedance 2.0 prompt package -- the exact block structure the old
reel-machine skill bundle's package-spec.md required (SETUP / RULE / IMAGE
REFERENCE MAP / PROMPT / VOICE & PACING / PER-SECOND TIMELINE / EFFECTS /
SCENE-LOCKS / NEGATIVE / PREFLIGHT / COST), filled in from a Teardown +
Shape + Look. This is the "template" LLM provider's implementation -- plain
string formatting, no API calls, always available.

Settings below are ported from settings-lock.md (the "settings lock" the
old bundle hardcoded into its WaveSpeed shell scripts): 9:16 vertical,
480p draft -> 720p final, native audio on, reference audio/video empty.
"""

from __future__ import annotations

from ofmhelpers.reel_machine.gender import DEFAULT_GENDER, get_gender
from ofmhelpers.reel_machine.looks import Look
from ofmhelpers.reel_machine.shapes import Shape, render_shape
from ofmhelpers.reel_machine.teardown import Teardown

SETTINGS = {
    "aspect_ratio": "9:16",
    "resolution_draft": "480p",
    "resolution_final": "720p",
    "generate_audio": True,
    "reference_audios": "EMPTY -- native voice, steered in the prompt (a reference audio makes the voice come out different each run)",
    "reference_videos": "EMPTY -- a reference video transfers motion AND look, diluting identity",
    "web_search": False,
}

BASE_NEGATIVE = (
    "fast/crammed speech, running lines together, robotic one-word delivery, monotone, "
    "changing pose, walking in/out, mirror/reflection selfie (unless the shape IS a "
    "mirror), doubled arms / second person's arm at the lens, face morphing, identity "
    "drift, warped mouth, plastic skin, extra fingers, invented cuts, camera cuts, "
    "changing outfit, readable text/displays in scene, text baked into the video, "
    "watermark, studio glamour, cinematic grading, gimbal smoothness, nudity"
)

PREFLIGHT_CHECKLIST = [
    "every second timed, every line tagged",
    "one camera holder",
    "lens described",
    "pose locked",
    "no person description (identity = reference images only)",
    "outfit in prompt",
    "word budget fits duration",
    "settings match the SETUP block",
    "brand-safe",
    "no internal names/slugs/file IDs inside the PROMPT text",
]


def _format_timeline(teardown: Teardown, gender) -> str:
    lines = []
    for beat in teardown.beats:
        tag = gender.tag if beat.speaker == "MAIN" else beat.speaker
        speaker = f"{tag} ({'on-cam' if beat.on_cam else 'off-cam'}, lip-sync)"
        lines.append(f'{beat.start:05.1f}-{beat.end:05.1f}s  {speaker}: "{beat.text}"')
    return "\n".join(lines)


def build_prompt_package(
    teardown: Teardown,
    shape: Shape,
    look: Look,
    duration: int,
    character_name: str = "your character",
    target: str = "",
    gender: str = DEFAULT_GENDER,
) -> str:
    """Deterministic first-draft prompt package. Meant to be reviewed and
    hand-edited (dialogue, camera_look, viral_mechanic) before generation --
    it fills the required structure, not the creative content.

    `target` is a persona/tone brief (e.g. "confident fitness coach pitching
    a program") -- it steers dialogue TONE only, never physical appearance;
    identity always comes from the reference images (see the RULE block
    below). `gender` picks which pronouns/voice-register the shape's
    templates render with (see gender.py / shapes.render_shape) -- it does
    not describe the character's looks either.
    """
    g = get_gender(gender)
    shape = render_shape(shape, g)
    preflight = "\n".join(f"  [ ] {item}" for item in PREFLIGHT_CHECKLIST)
    target_brief = target.strip() or (
        "(edit me) who/what this video is for -- persona, tone, vibe (e.g. "
        "'confident fitness coach pitching a program', 'girl-next-door CTA'). "
        "Never physical appearance -- identity comes from the reference images only."
    )

    return f"""SETUP
  Seedance 2.0 - Reference/text-to-video mode (NO start image) - refs (face first, 4-6) for {character_name}
  {duration}s - {SETTINGS["aspect_ratio"]} - {SETTINGS["resolution_draft"]} draft -> {SETTINGS["resolution_final"]} final -
  Generate Audio ON - Reference Audios {SETTINGS["reference_audios"]} -
  Reference Videos {SETTINGS["reference_videos"]} - Web Search OFF

TARGET / PERSONA
  Gender: {g.label} ({g.noun}) -- speaker tag {g.tag} in the timeline below.
  Brief: {target_brief}

RULE
  Describe ONLY scene + outfit + lens -- NEVER the person. Identity = the reference images only.

IMAGE REFERENCE MAP
  face refs -> face/identity - body sheet -> body - outfit refs (1-2) -> the outfit

SHAPE
  {shape.name} | voices: {shape.voices} | camera: {shape.camera}
  pacing: {shape.pacing}
  notes: {shape.notes}

PROMPT
  A continuous {duration}-second vertical {SETTINGS["aspect_ratio"]} video, authentic amateur UGC,
  {look.prompt}.
  Build {character_name}'s exact face, body and identity from the reference images and keep them
  identical the entire clip; never change {g.possessive} face.
  ONE stable pose the entire clip -- no walk-in, no walking, no pose change.
  (edit me) outfit: describe the exact, consistent outfit here.
  Mild grain, phone exposure, face sharp while the background stays slightly soft.
  ONE continuous take, no cuts, start mid-action.

VOICE & PACING
  {shape.voices}. Pacing: {shape.pacing}.
  Delivery tone matches the TARGET/PERSONA brief above.
  Always: natural connected speech, never robotic, never one word at a time; keep the
  voices clearly distinct.

PER-SECOND TIMELINE
{_format_timeline(teardown, g)}

EFFECTS
  1 Timeline: hook -> build -> signature beat -> landing (mark the SIGNATURE moment)
  2 Inventory: (edit me) the distinct effects used (handheld hold, push-in, micro-expression pause, ...)
  3 Density map: (edit me) LOW/MED/HIGH per 3-6s chunk
  4 Motion flow: (edit me) opening -> build -> resolution in one line each

SCENE-LOCKS
  [POSE] one stable pose the entire clip
  [CAMERA] {shape.camera}
  (edit me) [WALLS/ENV] / [PROP/SOUND] -- only the ones this scene needs

NEGATIVE
  {BASE_NEGATIVE},
  {look.negative}

PREFLIGHT
{preflight}

VIRAL MECHANIC
  {teardown.viral_mechanic}

CAMERA / LOOK
  {teardown.camera_look}

COST / RISK
  Draft {SETTINGS["resolution_draft"]} ~$1.50 - final {SETTINGS["resolution_final"]} ~$3 - never 1080p/4k -
  lip-sync slurs -> best visual + dub in edit - which line is THE trigger (regenerate rather than lose it)
"""
