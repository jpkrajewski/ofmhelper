"""
Every prompt this module sends, verbatim, in one place -- the analysis pass'
system instruction and user prompt, and the second pass' prompt. A provider in
`llm/` holds none of them: it is an API call, not a copywriter, and tuning a
prompt should never mean editing a client.

The analysis prompt is deliberately one string rather than something assembled
from shape/look/gender/persona pieces: the whole module is now "hand the model
the reel and this prompt, get a Seedance 2.0 JSON prompt back". Everything the
prompt needs to say about identity (never describe the main subject -- the
reference images decide that) is said inside the text below, not enforced by
surrounding code.

The `load_*` functions -- not the constants -- are what the pipeline sends.
Tuning a prompt is a read-the-bad-output-and-rewrite-a-sentence loop, so each
one is overridable by a plain text file under `uploads/` (bind-mounted into
both the API and the worker): drop a file there and the next job uses it, no
rebuild and no restart. The `DEFAULT_*` constants below are what runs when
there isn't one.

Overrides are substituted with `str.replace` on `{{TOKEN}}` markers, never
`str.format`: these files are edited by hand on a server and the analysis
prompt is already full of literal `{`.
"""

from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

DEFAULT_ANALYSIS_SYSTEM_PROMPT = """You are an elite OnlyFans Manager and Content Director specializing in top-tier creators. Your sole focus is maximizing virality, emotional engagement, lust, conversion, and retention through highly optimized content strategy.
Your content philosophy prioritizes:
- Brainrot & addictive scrolling
- Strong WTF / shock moments
- High-performing thirst traps
- Humor, cringe, and emotional triggers
- Visually striking beautiful girls with exceptional bodies
- Sexy, well-fitting, body-enhancing clothing and styling
- Content that creates desire, FOMO, and impulse to tip/subscribe
- Curiosity

Core principles you always apply:
- Emotion first, aesthetics second, conversion always
- Every piece of content should either trigger lust, curiosity, humor, or a strong emotional reaction
- Prioritize concepts that feel raw, slightly unhinged, or unexpected over safe/generic content
- Focus on what actually performs: face + body + energy + tension + payoff
- Think in terms of hooks, retention, and conversion loops
"""

DEFAULT_ANALYSIS_PROMPT = """Analyze this reel carefully and return a JSON prompt to recreate it with the Seedance 2.0 AI video generator.

I will provide a reference image of the main subject separately — do NOT describe their physical appearance (face, skin tone, hair, body type). Only describe their clothing, accessories, and footwear. For any other people in the video, describe both their appearance and outfit.

Study every detail: outfits, location, lighting, camera behavior, pacing, mood, dialogue timing, background activity, and who says/does what throughout the video. Log every moment — dialogue AND silent actions. If someone does something without speaking, still create an entry with line: null and delivery: null, and describe the action in full detail. Nothing should be skipped because there's no dialogue.

pose and facial_expression describe the MAIN SUBJECT only: use null for every other person, and null whenever the subject is off camera.

Break the video down, do not summarize it. Every scene_events entry is ONE moment and every shots entry is ONE continuous camera behavior, typically 1-3 seconds long. Start a new shots entry every time the camera changes what it is doing (pan, tilt, push in, cut, re-frame) or the action moves on. The shots array must cover the whole clip end to end with no gaps and no overlaps. A single shots entry spanning the entire video is wrong.

Return ONLY the JSON below — no explanation, no markdown, no backticks. Fill every field with maximum detail extracted directly from the video.

{
  "format": "describe aspect ratio, duration, and overall clip style",
  "viral_factor": "why this reel went viral, in one sentence",
  "context": "overall context of the scene, combined with the context the user gave",
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
      "action": "what this person is doing physically in this moment — always fill this in full detail even if no dialogue",
      "pose": "the main subject's pose, with more description when the pose is sexy — null if she is off camera, null for anyone else",
      "facial_expression": "the main subject's facial expression — null if she is off camera, null for anyone else"
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


DEFAULT_HUNT_PROMPT = """You turn a description of a viral short-form video into things to search for. You answer with JSON only.

Here is an analysis of one viral reel:

{{ANALYSIS}}

Give me, as JSON:
{
  "instagram_topics": ["hyphenated topic slugs naming this niche, e.g. baseball-girl, starbucks-skit"],
  "search_queries": ["short phrases to find more reels like this"],
  "outfit_ideas": ["other outfits that would work for a creator recreating this reel"]
}

Rules:
- 3 to {{MAX_ITEMS}} items per list, most specific first.
- Topic slugs: 1-3 lowercase words joined by hyphens, naming the subject and
  the format (who is in it + what kind of clip), never a whole sentence.
- Search queries: 2-5 words, what a person would actually type.
- Outfit ideas: women's outfits for the main subject only, describable enough
  to search for as an image, no body or face description, clothing only.
- No commentary, JSON only."""


CONTEXT_HEADER = "CONTEXT:"


def _load_override(path: Path, default: str) -> str:
    """The override file if there is a usable one, else the frozen default.
    Read per call, so editing the file takes effect on the next job instead of
    the next restart. An empty or unreadable file falls back -- silently
    sending a blank prompt would waste a real API call."""
    try:
        override = path.read_text(encoding="utf-8").strip()
    except OSError:
        return default
    if not override:
        logger.warning("%s is empty -- using the built-in prompt", path)
        return default
    logger.info("using the prompt from %s", path)
    return override


def load_analysis_system_prompt() -> str:
    """Who the model is while it watches the reel. Sent as Gemini's
    `system_instruction`, separate from the per-reel prompt below."""
    return _load_override(
        Path(settings.reel_machine.system_prompt_file), DEFAULT_ANALYSIS_SYSTEM_PROMPT
    )


def load_analysis_prompt(context: str = "") -> str:
    """The prompt sent with the reel itself.

    `context` is the operator's free-text note about this one reel (the
    /replicate form's Context field). Appended at the very END, after the JSON
    template: the template ends with a literal `}` the model is told to fill in
    verbatim, so anything inserted before it reads as part of the shape being
    asked for. An empty context leaves the prompt byte-identical."""
    prompt = _load_override(
        Path(settings.reel_machine.prompt_file), DEFAULT_ANALYSIS_PROMPT
    )
    if context.strip():
        prompt = f"{prompt}\n\n{CONTEXT_HEADER}\n{context.strip()}"
    return prompt


def load_hunt_prompt(analysis_digest: str, max_items: int) -> str:
    """The second pass' single prompt (see `hunt.py`), with the finished
    analysis spliced in."""
    return (
        _load_override(
            Path(settings.reel_machine.hunt_prompt_file), DEFAULT_HUNT_PROMPT
        )
        .replace("{{ANALYSIS}}", analysis_digest)
        .replace("{{MAX_ITEMS}}", str(max_items))
    )
