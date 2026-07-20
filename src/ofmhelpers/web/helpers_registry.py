"""
ofmhelpers/web/helpers_registry.py

Central list of "helper" tools available under /helpers.
Add one entry here whenever a new helper router is created --
everything else (nav, index page, jobs dashboard) is generic.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class HelperEntry:
    slug: str  # URL prefix under /helpers, e.g. "radio-comms"
    name: str  # Display name
    description: str  # One-line blurb for the index page


HELPERS: list[HelperEntry] = [
    HelperEntry(
        slug="radio-comms",
        name="Radio Comms Modulator",
        description="Turns clean TTS audio into crunchy CoD/CS-style radio comms.",
    ),
]
