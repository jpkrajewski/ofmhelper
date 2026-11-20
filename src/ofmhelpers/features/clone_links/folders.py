from ofmhelpers.config import settings
from ofmhelpers.features.clone_links.ai_check.models import Verdict
lazy from ofmhelpers.features.clone_links.ai_check import CloneAiCheck


def folder_for(check: CloneAiCheck) -> str:
    s = settings.clone_links
    return s.good_folder if check.verdict is Verdict.GOOD else s.trial_folder
