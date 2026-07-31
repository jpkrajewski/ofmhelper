"""What the second pass says to go looking for.

The cleaning rules live here rather than in `hunt.py` because they are part of
what a usable answer *is*: a topic that isn't a slug doesn't address a real
`instagram.com/popular/<slug>` page, and a duplicate is not a second idea.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from ofmhelpers.config import settings

# instagram.com/popular/<slug> topic pages are hyphenated words --
# "baseball-girl", "starbucks-skit". Anything else in a slug is dropped and
# runs of separators collapse, so a model answering "#Baseball Girl!" still
# lands on a real page.
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


class HuntIdeas(BaseModel):
    """What to go looking for. Every list is allowed to be empty -- that is
    the no-provider/failed-call case, not an error."""

    model_config = ConfigDict(extra="forbid")

    instagram_topics: list[str] = []
    search_queries: list[str] = []
    outfit_ideas: list[str] = []

    @property
    def is_empty(self) -> bool:
        return not (self.instagram_topics or self.search_queries or self.outfit_ideas)

    @staticmethod
    def _slug(value: str) -> str:
        return _SLUG_STRIP.sub("-", value.strip().lower()).strip("-")

    @classmethod
    def _clean_list(cls, values: object, *, as_slug: bool = False) -> list[str]:
        if not isinstance(values, list):
            return []
        cleaned: list[str] = []
        for value in values:
            if not isinstance(value, str):
                continue
            item = cls._slug(value) if as_slug else " ".join(value.split())
            if item and item not in cleaned:
                cleaned.append(item)
        return cleaned[: settings.reel_machine.hunt_max_items]

    @classmethod
    def from_llm_json(cls, data: object) -> HuntIdeas:
        """A parsed second-pass answer -> cleaned ideas. Never raises: a
        wrong-shaped answer is the same as no answer, and the caller falls
        back to terms derived from the analysis."""
        if not isinstance(data, dict):
            return cls()
        return cls(
            instagram_topics=cls._clean_list(
                data.get("instagram_topics"), as_slug=True
            ),
            search_queries=cls._clean_list(data.get("search_queries")),
            outfit_ideas=cls._clean_list(data.get("outfit_ideas")),
        )
