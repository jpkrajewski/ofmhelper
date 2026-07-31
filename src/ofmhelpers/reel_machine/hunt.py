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

The HTTP call lives in the provider, the prompt lives in `prompts.py`, and
what a usable answer looks like lives on `HuntIdeas` (`models/hunt.py`). What
is left here is the one thing that is actually this module's own: which parts
of the analysis are worth sending.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.reel_machine.llm.registry import get_text_provider
from ofmhelpers.reel_machine.models import HuntIdeas
from ofmhelpers.reel_machine.prompts import load_hunt_prompt

if TYPE_CHECKING:
    from ofmhelpers.reel_machine.models import ReelAnalysis

logger = get_logger(__name__)


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
            load_hunt_prompt(
                _analysis_digest(analysis), settings.reel_machine.hunt_max_items
            )
        )
        data = json.loads(raw)
    except Exception:
        logger.warning("hunt suggestions failed, falling back", exc_info=True)
        return HuntIdeas()

    return HuntIdeas.from_llm_json(data)
