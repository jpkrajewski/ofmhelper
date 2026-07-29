"""
Every HTTP route in the app, grouped by what it is *for*:

- `generation/` -- the AI generation tools (seedance, kling, nano banana pro,
  the no-cost fake_ai stand-in, the reel-cloning replicate pipeline) plus the
  unified picker page that fronts them.
- `downloads/`  -- pulling media in from elsewhere: videos, images, the
  metadata cleaner, and their picker page.
- `helpers/`    -- the small standalone tools on /helpers (ElevenLabs TTS,
  radio-comms FX, the scraper) plus the index that lists them.
- `admin/`      -- admin-only surfaces: the model roster, competition, file
  manager, action log, cookie upload.
- `workflow/`   -- the VA todo list and its public magic-link approval flow.

Three modules sit at this level because they belong to no single feature:
`auth` (login/logout), `refs` (the shared reference-file browser every
generation form uses), and `task_helpers` (the upload/job/serve plumbing
those routers are built out of).

`ROUTERS` below is the single registration list -- `web/main.py` loops over
it, so **adding a page means one import and one entry here**, and main.py is
never touched.
"""

from ofmhelpers.web.routers import auth, refs
from ofmhelpers.web.routers.admin import (
    action_log,
    competition,
    cookies,
    file_manager,
    models,
)
from ofmhelpers.web.routers.downloads import clean_image
from ofmhelpers.web.routers.downloads import images as download_images
from ofmhelpers.web.routers.downloads import index as downloads_index
from ofmhelpers.web.routers.downloads import videos as download_videos
from ofmhelpers.web.routers.generation import fake_ai, kling, nbp, replicate, seedance
from ofmhelpers.web.routers.generation import index as generation_index
from ofmhelpers.web.routers.helpers import elevenlabs, radio_comms, scraper
from ofmhelpers.web.routers.helpers import index as helpers_index
from ofmhelpers.web.routers.workflow import approve, todo

# Order is presentational only -- FastAPI matches on path and no two routers
# share a prefix -- so it follows the package grouping above and this list
# reads like the directory listing.
ROUTERS = [
    auth.router,
    refs.router,
    # generation/
    generation_index.router,
    seedance.router,
    kling.router,
    nbp.router,
    fake_ai.router,
    replicate.router,
    # downloads/
    downloads_index.router,
    download_videos.router,
    download_images.router,
    clean_image.router,
    # helpers/
    helpers_index.router,
    elevenlabs.router,
    radio_comms.router,
    scraper.router,
    # admin/
    models.router,
    competition.router,
    file_manager.router,
    action_log.router,
    cookies.router,
    # workflow/
    todo.router,
    approve.router,
]
