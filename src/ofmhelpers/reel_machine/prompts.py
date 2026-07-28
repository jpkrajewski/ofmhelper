"""
The single analysis prompt this module sends to the LLM, verbatim.

It is deliberately one string rather than something assembled from
shape/look/gender/persona pieces: the whole module is now "hand the model the
reel and this prompt, get a Seedance 2.0 JSON prompt back". Everything the
prompt needs to say about identity (never describe the main subject -- the
reference images decide that) is said inside the text below, not enforced by
surrounding code.

`load_analysis_prompt()` -- not the constant -- is what the pipeline sends.
Tuning this prompt is a read-the-bad-output-and-rewrite-a-sentence loop, so
it is overridable by a plain text file (`settings.reel_machine.prompt_file`,
default `uploads/analysis_prompt.txt`, which is bind-mounted into both the
API and the worker): drop a file there and the next job uses it, no rebuild
and no restart. `DEFAULT_ANALYSIS_PROMPT` below is what runs when there
isn't one.
"""

from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

DEFAULT_ANALYSIS_PROMPT = """Analyze this reel carefully and return a JSON prompt to recreate it with the Seedance 2.0 AI video generator.

I will provide a reference image of the main subject separately — do NOT describe their physical appearance (face, skin tone, hair, body type). Only describe their clothing, accessories, and footwear. For any other people in the video, describe both their appearance and outfit.

Study every detail: outfits, location, lighting, camera behavior, pacing, mood, dialogue timing, background activity, and who says/does what throughout the video. Log every moment — dialogue AND silent actions. If someone does something without speaking, still create an entry with line: null and delivery: null but describe the action in full detail. Nothing should be skipped because there's no dialogue.

Break the video down, do not summarize it. Every scene_events entry is ONE moment and every shots entry is ONE continuous camera behavior, typically 1-3 seconds long. Start a new shots entry every time the camera changes what it is doing (pan, tilt, push in, cut, re-frame) or the action moves on. The shots array must cover the whole clip end to end with no gaps and no overlaps. A single shots entry spanning the entire video is wrong.

Return ONLY the JSON below — no explanation, no markdown, no backticks. Fill every field with maximum detail extracted directly from the video.

{
  "format": "describe aspect ratio, duration, and overall clip style",
  "people": [
    {
      "id": "subject",
      "role": "main subject on camera",
      "appearance": "do not describe — reference image will be provided",
      "wardrobe": "every clothing item with color, material, fit, accessories, and footwear"
    },
    {
      "id": "person_2",
      "role": "describe their role — cameraman, friend, interviewer, passerby etc",
      "appearance": "full physical description since no reference image — only if they appear on screen",
      "wardrobe": "every clothing item with color, material, fit, accessories, and footwear"
    }
  ],
  "environment": "exact location type, time of day, indoor/outdoor, specific background elements, surfaces, colors, depth",
  "lighting": "light sources, direction, quality, color temperature, shadows, any exposure inconsistencies visible in the video",
  "color_grading": "describe the natural phone color profile — warm/cool/flat/overexposed, no cinematic grade",
  "atmosphere": "overall vibe, energy level, emotional tone of the scene",
  "audio": "all ambient sounds present — room tone, background noise, music if any, crowd, environment sounds",
  "pacing": "overall energy and rhythm — slow/conversational/quick/relaxed etc",
  "background_activity": "describe everything happening in the background — people, movement, objects, activity",
  "scene_events": [
    {
      "timestamp": "0:00",
      "speaker": "use the id from people array — subject, person_2 etc",
      "line": "exact words spoken verbatim, null if no dialogue",
      "delivery": "how it is said — tone, energy, casual/laughing/distracted etc, null if no dialogue",
      "action": "what this person is doing physically in this moment — always fill this in full detail even if no dialogue"
    }
  ],
  "style": "Raw casual iPhone TikTok footage. NOT cinematic. NOT stabilized. NOT influencer-style. Feels like a real person filmed this on their phone.",
  "camera_logic": "describe how the camera moves, angle, height, distance from subject, stability, any panning or following behavior",
  "imperfections": [
    "list every phone-camera artifact visible — autofocus hunting, exposure shifts, accidental cropping, camera drift, motion blur moments — with timestamps where possible"
  ],
  "shots": [
    {
      "time": "0:00–0:00",
      "action": "what is happening physically in this one shot",
      "camera_behavior": "exactly how the camera moves or holds in this one shot",
      "scene_event_cue": "the timestamp of the single scene_events entry this shot covers, e.g. 0:03 — just the timestamp, nothing else, or null if none"
    }
  ],
  "end_behavior": "clip ends abruptly mid-action like a casually uploaded viral TikTok",
  "negative_prompt": "no smooth gimbal motion, no cinematic stabilization, no professional lighting, no beauty filter, no ring light, no AI skin smoothing, no influencer posing, no perfectly centered framing, no overly dramatic acting, no model behavior, no slow motion, no music video energy, no fashion campaign aesthetic, no drone footage feel"
}"""


def load_analysis_prompt() -> str:
    """The prompt to send: the override file if there is one, else the frozen
    default. Read per call, so editing the file takes effect on the next job
    instead of the next restart. An empty or unreadable file falls back --
    silently sending a blank prompt would waste a real API call."""
    path = Path(settings.reel_machine.prompt_file)
    try:
        override = path.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_ANALYSIS_PROMPT
    if not override:
        logger.warning("%s is empty -- using the built-in analysis prompt", path)
        return DEFAULT_ANALYSIS_PROMPT
    logger.info("using the analysis prompt from %s", path)
    return override
