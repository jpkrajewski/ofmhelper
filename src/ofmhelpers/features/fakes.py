# Copyright (C) 2026 jpkrajewski

from ofmhelpers.config import settings
from ofmhelpers.integrations import outbound
lazy from ofmhelpers.integrations.ai_providers.apiyi.fake import routes as apiyi_routes
lazy from ofmhelpers.integrations.ai_providers.elyum.fake import routes as elyum_routes
lazy from ofmhelpers.integrations.ai_providers.higgsfield.fake import (
    routes as hf_routes,
)
lazy from ofmhelpers.integrations.ai_providers.kie.fake import routes as kie_routes
lazy from ofmhelpers.integrations.ai_providers.runninghub.fake import (
    routes as rh_routes,
)
lazy from ofmhelpers.integrations.apify.fake import routes as apify_routes
lazy from ofmhelpers.integrations.downloaders.savers_fake import routes as savers_routes
lazy from ofmhelpers.integrations.fanvue.fake import routes as fanvue_routes
lazy from ofmhelpers.integrations.instagram.fake import routes as instagram_routes
lazy from ofmhelpers.integrations.pinterest.fake import routes as pinterest_routes
lazy from ofmhelpers.integrations.senders.telegram.fake import routes as telegram_routes
lazy from ofmhelpers.integrations.short_links.fake import routes as links_routes
lazy from ofmhelpers.integrations.signin.fake import routes as signin_routes
lazy from ofmhelpers.integrations.snapchat.fake import routes as snapchat_routes
lazy from ofmhelpers.integrations.stacked.fake import routes as stacked_routes
lazy from ofmhelpers.integrations.x.fake import routes as x_routes


def install_fakes() -> None:
    if not settings.infra.fake_externals:
        return
    outbound.register_fakes(
        [
            *instagram_routes(),
            *kie_routes(),
            *hf_routes(),
            *signin_routes(),
            *links_routes(),
            *savers_routes(),
            *apify_routes(),
            *stacked_routes(),
            *fanvue_routes(),
            *rh_routes(),
            *elyum_routes(),
            *apiyi_routes(),
            *telegram_routes(),
            *pinterest_routes(),
            *snapchat_routes(),
            *x_routes(),
        ],
    )
