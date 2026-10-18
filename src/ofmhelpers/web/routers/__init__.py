"""
Every HTTP route in the app, grouped by what it is *for*:

- `generation/` -- AI generation: `ai.py` runs every model of every provider
  (aigenproviders/), `index.py` is its /generate page, `clone.py` is /clone,
  and `legacy.py` keeps the job pages of the per-model tools it replaced.
- `downloads/`  -- pulling media in from elsewhere: videos, images, the
  metadata cleaner, their picker page, and the viral-reel finder.
- `image_tools/` -- local image edits with no external service (character
  sheet, admin only).
- `video_tools/` -- local video edits with no external service (uniqify).
- `admin/`      -- admin-only surfaces: landing-page applications, file
  manager, action log, cookie upload.

Four modules sit at this level because they belong to no single feature:
`auth` (login/logout), `apply` (the public landing-page application form's
POST target -- its CRM view is `admin/applications.py`), `refs` (the shared
reference-file browser every generation form uses), and `task_helpers` (the
upload/job/serve plumbing those routers are built out of).

`ROUTERS` below is the single registration list -- `web/main.py` loops over
it, so **adding a page means one import and one entry here**, and main.py is
never touched.
"""

from ofmhelpers.web.routers import apply, auth, refs
from ofmhelpers.web.routers.admin import action_log, cookies, file_manager
from ofmhelpers.web.routers.admin import applications as admin_applications
from ofmhelpers.web.routers.downloads import clean_image, reels
from ofmhelpers.web.routers.downloads import images as download_images
from ofmhelpers.web.routers.downloads import index as downloads_index
from ofmhelpers.web.routers.downloads import videos as download_videos
from ofmhelpers.web.routers.generation import ai, clone
from ofmhelpers.web.routers.generation import index as generation_index
from ofmhelpers.web.routers.generation.legacy import LEGACY_TOOLS
from ofmhelpers.web.routers.image_tools import character_sheet
from ofmhelpers.web.routers.video_tools import uniqify

# Order is presentational only -- FastAPI matches on path and no two routers
# share a prefix -- so it follows the package grouping above and this list
# reads like the directory listing.
ROUTERS = [
    auth.router,
    apply.router,
    refs.router,
    # generation/
    generation_index.router,
    ai.router,
    clone.router,
    *(router for _label, router in LEGACY_TOOLS.values()),
    # downloads/
    downloads_index.router,
    download_videos.router,
    download_images.router,
    clean_image.router,
    reels.router,
    # image_tools/
    character_sheet.router,
    # video_tools/
    uniqify.router,
    # admin/
    admin_applications.router,
    file_manager.router,
    action_log.router,
    cookies.router,
]
