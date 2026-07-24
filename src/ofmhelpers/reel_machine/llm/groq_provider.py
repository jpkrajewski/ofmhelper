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
import os
from pathlib import Path

from ofmhelpers.reel_machine.gender import DEFAULT_GENDER
from ofmhelpers.reel_machine.looks import Look
from ofmhelpers.reel_machine.prompt_builder import build_prompt_package
from ofmhelpers.reel_machine.shapes import Shape
from ofmhelpers.reel_machine.teardown import Teardown

WRITE_SYSTEM_PROMPT = (
    "You punch up Seedance 2.0 video-generation prompt packages for a reel-cloning "
    "pipeline. Keep the EXACT block structure and every setting in the draft "
    "unchanged (SETUP/TARGET/PERSONA/RULE/IMAGE REFERENCE MAP/PROMPT/VOICE & "
    "PACING/PER-SECOND TIMELINE/EFFECTS/SCENE-LOCKS/NEGATIVE/PREFLIGHT/COST). Only "
    "rewrite the dialogue lines, hook, and scene description to feel sharper and "
    "more natural, matching the TARGET/PERSONA block's tone/gender exactly -- never "
    "change the gender, pronouns, or speaker tag (SHE/HE/THEY) already in the draft, "
    "and never describe the character's physical appearance (identity comes from "
    "the reference images only, per the RULE block). Never change durations, "
    "resolutions, aspect ratios, or any other setting. Return only the rewritten "
    "package, no commentary."
)

ANALYZE_SYSTEM_PROMPT = (
    "You are tearing down a viral short-form video reel so its FORMAT (not its "
    "person, face, or exact words) can be rebuilt with a different character. You "
    "are given a contact-sheet image (one frame per second, tiled) and its word-level "
    "transcript. Identify: (1) the hook -- what happens in the first ~1s that stops "
    "the scroll; (2) the viral mechanic -- the one-sentence reason this format works "
    "(e.g. call-out hook -> withhold -> debatable twist -> comment bait); (3) the "
    "camera look -- lens character, vignette/fisheye strength, camera height, who "
    "holds it, color character, motion (handheld vs static), described precisely "
    "enough to steer a text-to-video prompt. Respond with ONLY strict JSON: "
    '{"hook": "...", "viral_mechanic": "...", "camera_look": "..."}'
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
        self.api_key = api_key or os.environ["GROQ_API_KEY"]
        self.text_model = text_model
        self.vision_model = vision_model or os.getenv("GROQ_VISION_MODEL")

    def analyze_reel(self, contact_sheet: Path, transcript_text: str) -> dict:
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
        return response.choices[0].message.content or draft
