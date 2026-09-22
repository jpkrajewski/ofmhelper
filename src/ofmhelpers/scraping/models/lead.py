"""A qualified lead: an Instagram profile that passed the follower band and
has an OnlyFans presence, plus how sure we are of that.

The confidence tier is the whole value of the row -- a CERTAIN lead can be
approached straight away, a LIKELY one needs ten seconds of eyeballing
first -- so it is a field on the model, not a comment in the exporter.
"""

import enum

from pydantic import BaseModel, ConfigDict

from ofmhelpers.scraping.models.profile import InstagramProfile


class Confidence(enum.StrEnum):
    # An onlyfans.com URL was found: in the bio, in a link field, or on the
    # link-aggregator page the bio pointed at.
    CERTAIN = "CERTAIN"
    # The bio names OnlyFans, or points at an aggregator we could not read,
    # but no actual onlyfans.com URL was ever seen.
    LIKELY = "LIKELY"


class Lead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: InstagramProfile
    confidence: Confidence
    # The onlyfans.com URL, when one was actually found. None on a LIKELY row.
    onlyfans_url: str | None = None
    # Why this row is here in one short phrase ("bio link", "linktr.ee/x",
    # "bio mentions OnlyFans") -- the reviewer's shortcut to deciding whether
    # a LIKELY lead is worth the click.
    evidence: str = ""

    def sort_key(self) -> tuple[int, int]:
        """CERTAIN before LIKELY, then biggest audience first -- the order a
        human wants to work the sheet in."""
        return (
            0 if self.confidence is Confidence.CERTAIN else 1,
            -(self.profile.followers or 0),
        )
