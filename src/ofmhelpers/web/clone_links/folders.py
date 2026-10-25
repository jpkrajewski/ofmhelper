"""Where a reviewed /clone-links video goes in its owner's Drive root: a
GOOD one into `settings.clone_links.good_folder`, anything else -- GLITCHY,
or a check that could not answer -- into `trial_folder`, where a person
looks first. Import-light: /drive reads `review_folders` too."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ofmhelpers.clone_ai_check.models import Verdict
from ofmhelpers.config import settings

if TYPE_CHECKING:
    from ofmhelpers.clone_ai_check import CloneAiCheck


def folder_for(check: CloneAiCheck) -> str:
    s = settings.clone_links
    return s.good_folder if check.verdict is Verdict.GOOD else s.trial_folder


def review_folders() -> set[str]:
    """The folder names reviews upload into (no account's folder)."""
    s = settings.clone_links
    return {s.good_folder, s.trial_folder}
