from ofmhelpers.db.self_links import store as self_links
lazy from ofmhelpers.db.short_links.schema import BridgeTarget, SelfClick


def active_hosts() -> list[str]:
    return self_links.active_hosts()


def bridge_target(host: str, slug: str) -> BridgeTarget | None:
    return self_links.bridge_target(host, slug)


def record_click(click: SelfClick) -> None:
    self_links.record_click(click)
