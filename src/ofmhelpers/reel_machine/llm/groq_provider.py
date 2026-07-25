"""
Free-tier LLM option: uses Groq (OpenAI-compatible API, generous free rate
limits) for the text half of the job --

- write_prompt_package: sends the template draft to a fast text model to
  punch up the dialogue while keeping every block/setting locked in place.
  This works fine on Groq's free tier (default model: llama-3.3-70b-versatile).
- analyze_reel: VISION. As of this writing, Groq's model catalog has NO
  vision-capable model at all (confirmed against the live GET /models
  response -- no llama-vision, no llama-4-scout, nothing that accepts image
  input), so this is a no-op here, same as TemplateProvider -- it does NOT
  attempt a doomed API call by default. If Groq adds a vision model back (or
  your account gets access to one), pass its id as `vision_model` (or set
  GROQ_VISION_MODEL) to turn this on. See llm/gemini_provider.py for a free
  provider that DOES do real vision analysis today.

Requires GROQ_API_KEY (https://console.groq.com -- free signup, no card
required for the free tier) and the `groq` package (a base dependency of
this project, see pyproject.toml).
"""

import base64
import json
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.reel_machine.gender import DEFAULT_GENDER
from ofmhelpers.reel_machine.llm.base import strip_llm_preamble
from ofmhelpers.reel_machine.looks import Look
from ofmhelpers.reel_machine.prompt_builder import build_prompt_package
from ofmhelpers.reel_machine.shapes import Shape
from ofmhelpers.reel_machine.teardown import Teardown

WRITE_SYSTEM_PROMPT = (
    "You punch up Seedance 2.0 video-generation prompt packages for a reel-cloning "
    "pipeline. Keep the EXACT block structure and every setting in the draft "
    "unchanged (SETUP/IMAGE REFERENCE MAP/PROMPT/VOICE & PACING/PER-SECOND "
    "TIMELINE/EFFECTS/SCENE-LOCKS/NEGATIVE). Only rewrite the dialogue lines, "
    "hook, and scene description to feel sharper and more natural -- never "
    "change the gender, pronouns, or speaker tag (SHE/HE/THEY) already in the "
    "draft, and never describe the character's physical appearance (identity "
    "comes from the reference images only). "
    "CRITICAL -- preserve the subject's actual motion exactly: the draft's "
    "PROMPT block describes precisely how the main subject moves for the "
    "whole clip (e.g. walking away from camera, back turned) -- never flatten "
    "this into a generic 'one stable pose' / static description, and never "
    "add a pose-lock phrase that contradicts motion the draft already "
    "describes. "
    "CRITICAL -- do not invent: the hook/viral_mechanic/camera_look/PER-SECOND "
    "TIMELINE/EFFECTS/SCENE-LOCKS already describe the REAL actions, props, "
    "setting, and camera movement observed in the source reel. You may only "
    "rephrase/polish that existing content for clarity and flow -- never "
    "introduce a new action, prop, location, or camera move that isn't already "
    "present in the draft you were given, even if it would read as more "
    "'viral' or entertaining. If the draft's scene is sparse or plain, keep it "
    "sparse and plain rather than filling the gap with an invented scenario. "
    "Never change durations, resolutions, aspect ratios, or any other setting. "
    "Return only the rewritten package, no commentary."
)

ANALYZE_SYSTEM_PROMPT = (
    "You are tearing down a viral short-form video reel so its FORMAT (not its "
    "person, face, or exact words) can be rebuilt with a different character. You "
    "are given either the full video or a contact-sheet image (one frame per "
    "second, tiled), plus its word-level transcript.\n\n"
    "STEP 1 -- Identify the main subject: if more than one person or notable "
    "moving object appears, first work out which one is the MAIN SUBJECT -- the "
    "one whose actions actually drive the format (usually who's speaking, closest "
    "to camera, or centered in frame). Describe that subject only: gender (only "
    "if visually obvious), approximate age range, clothing, hairstyle, "
    "accessories, body position, and camera framing. This is an internal anchor "
    "so you track the right subject in step 2 -- it is NOT a physical description "
    "for the final video (identity there always comes from separate reference "
    "images, never from this description).\n\n"
    "STEP 2 -- Using that subject as the anchor, identify: (1) the hook -- what "
    "happens in the first ~1s that stops the scroll; (2) the subject_action -- "
    "EXACTLY what the main subject physically does across the ENTIRE clip, "
    'moment by moment (e.g. "walks away from the camera the entire clip, back '
    'turned, never facing it, steady unbroken stride, no pause"). This is the '
    "single most important field: if the subject walks, say it walks -- never "
    "default to describing a static/stationary pose unless the subject is "
    "truly motionless the whole time. Also note any OTHER on-screen "
    "person/object whose action matters to the format (e.g. a background "
    "bystander who reacts, stares, or falls) and specifically which one does "
    'what, in what order, if there is more than one (e.g. "the first '
    "bystander stares and does a double-take; a few seconds later the SECOND, "
    'separate bystander behind him trips and falls"); (3) the viral mechanic '
    "-- the one-sentence reason this format works (e.g. call-out hook -> "
    "withhold -> debatable twist -> comment bait); (4) the camera look -- ONLY "
    "the camera itself: lens character, vignette/fisheye strength, camera "
    "height, who holds it (or if it's static/tripod/mounted), color "
    "character, motion (handheld vs static). Do not describe the SUBJECT's "
    "environment here (e.g. if the camera operator happens to be seated at a "
    "cafe table but films a subject walking down a street, the camera_look is "
    "'static, eye-level, filmed from a seated vantage point' -- the street is "
    "where the ACTION happens, not the camera's own location; never conflate "
    "the two). Never invent an action, prop, or location you don't actually "
    "see happen ON SCREEN. If nothing dramatic happens beyond the main "
    "subject's own motion, say so plainly rather than inventing a twist. "
    "Describe camera movement and framing precisely enough to steer a "
    "text-to-video prompt.\n\n"
    "Respond with ONLY strict JSON, no commentary before or after: "
    '{"main_subject": "...", "hook": "...", "subject_action": "...", '
    '"viral_mechanic": "...", "camera_look": "..."}'
)


def _image_data_url(contact_sheet: Path) -> str:
    data = base64.b64encode(contact_sheet.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{data}"


class GroqProvider:
    name = "groq"

    def __init__(
        self,
        api_key: str | None = None,
        text_model: str = "llama-3.3-70b-versatile",
        vision_model: str | None = None,
    ):
        s = settings.reel_machine
        self.api_key = api_key or s.groq_api_key
        if self.api_key is None:
            raise KeyError("GROQ_API_KEY")
        self.text_model = text_model
        self.vision_model = vision_model or s.groq_vision_model

    def analyze_reel(
        self,
        contact_sheet: Path,
        transcript_text: str,
        video_path: Path | None = None,
    ) -> dict:
        # Groq's current model catalog has no vision-capable model at all
        # (see module docstring) -- video_path is accepted only to satisfy
        # the shared LLMProvider interface, never used.
        if not self.vision_model:
            print(
                "[reel_machine] groq has no vision model configured (none is "
                "available on the free tier as of this writing) -- skipping "
                "vision analysis, keeping the (edit me) placeholders. Use the "
                "gemini provider for free vision analysis instead.",
                flush=True,
            )
            return {}

        from groq import Groq

        client = Groq(api_key=self.api_key)
        response = client.chat.completions.create(
            model=self.vision_model,
            messages=[
                {"role": "system", "content": ANALYZE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Transcript:\n{transcript_text}"},
                        {
                            "type": "image_url",
                            "image_url": {"url": _image_data_url(contact_sheet)},
                        },
                    ],
                },
            ],
            temperature=0.4,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content or "{}")

    def write_prompt_package(
        self,
        teardown: Teardown,
        shape: Shape,
        look: Look,
        duration: int,
        target: str = "",
        gender: str = DEFAULT_GENDER,
    ) -> str:
        from groq import Groq

        draft = build_prompt_package(
            teardown, shape, look, duration, target=target, gender=gender
        )
        client = Groq(api_key=self.api_key)
        response = client.chat.completions.create(
            model=self.text_model,
            messages=[
                {"role": "system", "content": WRITE_SYSTEM_PROMPT},
                {"role": "user", "content": draft},
            ],
            temperature=0.7,
        )
        return strip_llm_preamble(response.choices[0].message.content or draft, draft)
