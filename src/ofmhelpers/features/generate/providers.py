from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import ParamHistory

CLONE_TASKS = frozenset({"clone", "clone_links", "clone_long"})


def start_provider(actor: str, tasks: frozenset[str], offered: list[str]) -> str:
    if used := ParamHistory(tasks=tasks, actor=actor, field="provider").last(offered):
        return used
    wanted = settings.aigen.default_provider
    return wanted if wanted in offered else offered[0]
