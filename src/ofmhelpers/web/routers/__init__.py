lazy from types import ModuleType

from fastapi import APIRouter

from ofmhelpers.config.retired import retired
from ofmhelpers.web.routers import (
    account,
    ai_files,
    apply,
    auth,
    chatting,
    cookies,
    distribution,
    home,
    jobs,
    legal,
    notifications,
    refs,
    vas,
    webhooks,
)
from ofmhelpers.web.routers.admin import action_log, file_manager
from ofmhelpers.web.routers.admin import applications as admin_applications
from ofmhelpers.web.routers.admin import styleguide as admin_styleguide
from ofmhelpers.web.routers.admin import users as admin_users
from ofmhelpers.web.routers.downloads import clean_image, reels, viral
from ofmhelpers.web.routers.downloads import images as download_images
from ofmhelpers.web.routers.downloads import index as downloads_index
from ofmhelpers.web.routers.downloads import media as download_media
from ofmhelpers.web.routers.downloads import videos as download_videos
from ofmhelpers.web.routers.generation import ai, clone, clone_links, clone_long
from ofmhelpers.web.routers.generation import index as generation_index
from ofmhelpers.web.routers.generation.legacy import LEGACY_TOOLS
from ofmhelpers.web.routers.image_tools import character_sheet, pictures, threads
from ofmhelpers.web.routers.inventory import drive, handmade, phones, storage
from ofmhelpers.web.routers.tracking import accounts, links, self_links
from ofmhelpers.web.routers.video_tools import edit, repurpose, trials, uniqify


def _live(modules: list[ModuleType]) -> list[APIRouter]:
    gone = retired().routers
    return [m.router for m in modules if m.__name__.rsplit(".", 1)[-1] not in gone]


ROUTERS = [
    home.router,
    auth.router,
    account.router,
    cookies.router,
    vas.router,
    chatting.router,
    notifications.router,
    webhooks.router,
    ai_files.router,
    jobs.router,
    apply.router,
    legal.router,
    refs.router,
    generation_index.router,
    ai.router,
    clone.router,
    clone_long.router,
    *_live([clone_links]),
    *(router for _label, router in LEGACY_TOOLS.values()),
    downloads_index.router,
    download_media.router,
    download_videos.router,
    download_images.router,
    clean_image.router,
    *_live([reels]),
    viral.router,
    accounts.router,
    links.router,
    self_links.router,
    phones.router,
    drive.router,
    storage.router,
    handmade.router,
    character_sheet.router,
    *_live([pictures]),
    threads.router,
    uniqify.router,
    edit.router,
    *_live([repurpose]),
    trials.router,
    distribution.router,
    admin_users.router,
    admin_applications.router,
    admin_styleguide.router,
    file_manager.router,
    action_log.router,
]
