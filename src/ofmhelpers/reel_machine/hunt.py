"""
Second, cheap LLM pass over a finished analysis: what to go looking for next.

Gemini watches the reel and describes it; that description is prose, and
prose makes bad search terms -- "the D Las Vegas, a hotel and casino located
on the Fremont Street Experience" is not what anyone types into Instagram.
This module hands the finished analysis to a text model (see
`llm/registry.get_text_provider`) and asks for the three lists a VA actually
needs:

  - `instagram_topics` -> slugs for instagram.com/popular/<slug>, the topic
                          pages that actually show this niche
  - `search_queries`   -> phrases to search on TikTok
  - `outfit_ideas`     -> other outfits the main subject could wear

It is deliberately best-effort: no provider configured, a rate limit, a bad
answer, anything at all -> empty lists, and the caller falls back to the terms
derived mechanically from the analysis (see `_outfit_searches` /
`_reel_searches` in `web/routers/generation/replicate.py`). This runs *after*
the reel is downloaded and analyzed, so failing the job here would throw away
real work for a nice-to-have.

The HTTP call lives in the provider and the prompt lives in `prompts.py`;
what is left here is the one thing that is actually this module's own: which
parts of the analysis are worth sending, and what a usable answer looks like
coming back.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ofmhelpers.log import get_logger
from ofmhelpers.reel_machine.llm.registry import get_text_provider
from ofmhelpers.reel_machine.prompts import load_hunt_prompt

if TYPE_CHECKING:
    from ofmhelpers.reel_machine.schema import ReelAnalysis

logger = get_logger(__name__)

_MAX_ITEMS = 6
# instagram.com/popular/<slug> topic pages are hyphenated words --
# "baseball-girl", "starbucks-skit". Anything else in a slug is dropped and
# runs of separators collapse, so a model answering "#Baseball Girl!" still
# lands on a real page.
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


@dataclass
class HuntIdeas:
    """What to go looking for. Every list is allowed to be empty -- that is
    the no-provider/failed-call case, not an error."""

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
    """Ask the text model what to search for. Never raises: every failure path
    returns empty lists and the caller uses its own derived terms."""
    if analysis is None:
        return HuntIdeas()
    provider = get_text_provider()
    if provider is None:
        return HuntIdeas()

    try:
        raw = provider.complete_json(
            load_hunt_prompt(_analysis_digest(analysis), _MAX_ITEMS)
        )
        data = json.loads(raw)
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
