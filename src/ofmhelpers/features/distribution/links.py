# Copyright (C) 2026 jpkrajewski

from ofmhelpers.db.distribution import store
from ofmhelpers.db.self_links import store as self_links
from ofmhelpers.db.short_links import store as short_links
from ofmhelpers.db.short_links.catalog import provider_infos
from ofmhelpers.db.short_links.schema import PlannedLink
from ofmhelpers.features.links import sync
from ofmhelpers.features.refusals import RefusedError
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.distribution.schema import ModelPreset

logger = get_logger(__name__)

PROVIDER = "self"


def model_link(user_id: int, model: ModelPreset) -> str:
    found = next(
        (m for m in store.repository().model_links(user_id) if m.preset == model.key),
        None,
    )
    if found is not None:
        return found.url
    if model.domain not in self_links.active_hosts(user_id):
        msg = f"{model.domain} is not one of your self link domains (Self links page)"
        raise RefusedError(msg)
    rules = provider_infos()[PROVIDER].slug
    taken = short_links.taken_slugs(user_id, PROVIDER, model.domain)
    slug, n = rules.make(model.slug), 1
    while slug in taken:
        n += 1
        slug = rules.numbered(rules.make(model.slug), n)
    planned = PlannedLink(
        slug=slug,
        domain=model.domain,
        destination=str(model.destination),
        target=model.target,
        creator=model.creator,
    )
    ids = short_links.add_creating(user_id, PROVIDER, [planned])
    sync.create_links(user_id, PROVIDER, ids)
    made = next(iter(short_links.get_links(ids, user_id)), None)
    if made is None or not made.url:
        msg = f"The self link for {model.title} could not be made"
        raise RefusedError(msg)
    store.repository().set_model_link(user_id, model.key, made.id)
    logger.info(
        "distribution: %s got its self link %s -> %s",
        model.key,
        made.url,
        model.destination,
    )
    return made.url
