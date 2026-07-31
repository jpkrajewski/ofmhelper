"""
Second, cheap LLM pass over a finished analysis: what to go looking for next.

Gemini watches the reel and describes it; that description is prose, and
prose makes bad search terms -- "the D Las Vegas, a hotel and casino located
on the Fremont Street Experience" is not what anyone types into Instagram.
This module hands the finished analysis to a **free text model** (Groq) and
asks for the three lists a VA actually needs:

  - `instagram_topics` -> slugs for instagram.com/popular/<slug>, the topic
                          pages that actually show this niche
  - `search_queries`   -> phrases to search on TikTok
  - `outfit_ideas`     -> other outfits the main subject could wear

It is deliberately best-effort: no key, a rate limit, a bad answer, anything
at all -> empty lists, and the caller falls back to the terms derived
mechanically from the analysis (see `_outfit_searches` / `_reel_searches` in
`web/routers/generation/replicate.py`). This runs *after* the reel is
downloaded and analyzed, so failing the job here would throw away real work
for a nice-to-have.

Groq's API is OpenAI-shaped (`/openai/v1/chat/completions`), so this is one
plain `requests` POST -- no SDK, in keeping with `aigenproviders/kaiai`.
GROQ_API_KEY comes from https://console.groq.com (free, no card);
GROQ_MODEL overrides the model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import requests

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

if TYPE_CHECKING:
    from ofmhelpers.reel_machine.schema import ReelAnalysis

logger = get_logger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_TIMEOUT_S = 20
_MAX_ITEMS = 6
# instagram.com/popular/<slug> topic pages are hyphenated words --
# "baseball-girl", "starbucks-skit". Anything else in a slug is dropped and
# runs of separators collapse, so a model answering "#Baseball Girl!" still
# lands on a real page.
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")

_SYSTEM_PROMPT = (
    "You turn a description of a viral short-form video into things to search "
    "for. You answer with JSON only."
)

_USER_PROMPT = """Here is an analysis of one viral reel:

{analysis}

Give me, as JSON:
{{
  "instagram_topics": ["hyphenated topic slugs naming this niche, e.g. baseball-girl, starbucks-skit"],
  "search_queries": ["short phrases to find more reels like this"],
  "outfit_ideas": ["other outfits that would work for a creator recreating this reel"]
}}

Rules:
- 3 to {max_items} items per list, most specific first.
- Topic slugs: 1-3 lowercase words joined by hyphens, naming the subject and
  the format (who is in it + what kind of clip), never a whole sentence.
- Search queries: 2-5 words, what a person would actually type.
- Outfit ideas: women's outfits for the main subject only, describable enough
  to search for as an image, no body or face description, clothing only.
- No commentary, JSON only."""


@dataclass
class HuntIdeas:
    """What to go looking for. Every list is allowed to be empty -- that is
    the no-key/failed-call case, not an error."""

    instagram_topics: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    outfit_ideas: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.instagram_topics or self.search_queries or self.outfit_ideas)

    def to_dict(self) -> dict:
        return {
            "instagram_topics": self.instagram_topics,
            "search_queries": self.search_queries,
            "outfit_ideas": self.outfit_ideas,
        }


def _analysis_digest(analysis: ReelAnalysis) -> str:
    """The parts of the analysis that say what the reel *is* -- the per-second
    timeline and camera notes describe how to shoot it, which tells you
    nothing about what to search for.

    Only the main subject's wardrobe goes in. The other `people` entries are
    the cameraman and whoever walked past ("not visible", "off-camera"), and
    feeding those in produced outfit ideas for nobody."""
    subject = analysis.subject
    return json.dumps(
        {
            "viral_factor": analysis.viral_factor,
            "context": analysis.context,
            "format": analysis.format,
            "environment": analysis.environment,
            "style": analysis.style,
            "subject_wardrobe": subject.wardrobe if subject else "",
        },
        ensure_ascii=False,
    )


def _clean_slug(value: str) -> str:
    return _SLUG_STRIP.sub("-", value.strip().lower()).strip("-")


def _clean_list(values: object, *, as_slug: bool = False) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned = []
    for value in values:
        if not isinstance(value, str):
            continue
        item = _clean_slug(value) if as_slug else " ".join(value.split())
        if item and item not in cleaned:
            cleaned.append(item)
    return cleaned[:_MAX_ITEMS]


def suggest_hunt(analysis: ReelAnalysis | None) -> HuntIdeas:
    """Ask the free model what to search for. Never raises: every failure path
    returns empty lists and the caller uses its own derived terms."""
    if analysis is None:
        return HuntIdeas()
    s = settings.reel_machine
    if not s.groq_api_key:
        logger.info("no GROQ_API_KEY, skipping the hunt suggestions")
        return HuntIdeas()

    payload: dict = {
        "model": s.groq_model,
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _USER_PROMPT.format(
                    analysis=_analysis_digest(analysis), max_items=_MAX_ITEMS
                ),
            },
        ],
    }
    try:
        response = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {s.groq_api_key}"},
            json=payload,
            timeout=_TIMEOUT_S,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
    except Exception:
        logger.warning("hunt suggestions failed, falling back", exc_info=True)
        return HuntIdeas()

    if not isinstance(data, dict):
        return HuntIdeas()
    return HuntIdeas(
        instagram_topics=_clean_list(data.get("instagram_topics"), as_slug=True),
        search_queries=_clean_list(data.get("search_queries")),
        outfit_ideas=_clean_list(data.get("outfit_ideas")),
    )
