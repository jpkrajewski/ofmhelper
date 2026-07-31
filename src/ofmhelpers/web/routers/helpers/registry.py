"""
ofmhelpers/web/routers/helpers/registry.py

Central list of "helper" tools available under /helpers.
Add one entry here whenever a new helper router is created --
everything else (nav, index page, jobs dashboard) is generic.
"""

from pydantic import BaseModel, ConfigDict


class HelperEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str  # URL prefix under /helpers, e.g. "radio-comms"
    name: str  # Display name
    description: str  # One-line blurb for the index page


HELPERS: list[HelperEntry] = [
    HelperEntry(
        slug="radio-comms",
        name="Radio Comms Modulator",
        description="Turns clean TTS audio into crunchy CoD/CS-style radio comms.",
    ),
    HelperEntry(
        slug="elevenlabs",
        name="ElevenLabs TTS",
        description="Text-to-speech generation via ElevenLabs.",
    ),
    HelperEntry(
        slug="scraper",
        name="Social Scraper",
        description="Scrapes and ranks Instagram/TikTok profiles into a spreadsheet.",
    ),
]
