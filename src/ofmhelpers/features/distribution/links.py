"""Each model's one self link (owner 2026-11-18: "links are per model not
per account"): a bridge on the model's domain (`<slug>`, numbered when
taken) to the model's page, made the first time one of its files is
pinned, the way /links' Generate makes one (`short_links.add_creating` +
the self provider), so /links and /self-links list and count it too. Every
pin of the model that carries a link carries this one."""

from ofmhelpers.db.stores import distribution as store
from ofmhelpers.db.stores import self_links, short_links
from ofmhelpers.features.links import sync
from ofmhelpers.features.refusals import RefusedError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.link_catalog import provider_infos
from ofmhelpers.schemas.links import PlannedLink
lazy from ofmhelpers.schemas.distribution import ModelPreset

logger = get_logger(__name__)

PROVIDER = "self"


def model_link(user_id: int, model: ModelPreset) -> str:
    """The model's self link's address: its own, or one made now (the first
    pin of the model). Raises `RefusedError` when the model's domain is not
    one of the owner's self link domains."""
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
