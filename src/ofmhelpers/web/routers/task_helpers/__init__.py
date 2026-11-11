"""
Shared plumbing for every "upload files -> run in background -> download
result" router (/ai, /clone, /clone-long, /download-media, /clean-images,
/uniqify, /character-sheet and the old per-model tools' job pages).

Each router should only need to define its own processing function and
wire it through these helpers -- everything else lives here once, split by
what it does:

- `uploads.py`    the asset store as routes use it (the store itself:
                  `features/assets/store.py`; its refusals become 400s here)
- `manifests.py`  a picker's new + reused entries into paths
                  (`features/assets/manifests.py`), refusals as 400s
- `responses.py`  the status/polling payloads every tool renders (the
                  result cards: `features/assets/cards.py`)
- `serving.py`    handing a stored file back to the browser
- `routes.py`     the status page / polling / download endpoints of a job
- `features/assets/names.py` (re-exported)  what a downloaded result is saved as

Import from this package, not the submodules: every tool router imports from
here, and which submodule a helper lives in is not their business.
"""

from ofmhelpers.features.assets.names import download_name
from ofmhelpers.web.routers.task_helpers.manifests import (
    build_ordered_paths,
    keep_files,
    keep_videos,
    resolve_existing_ref,
)
from ofmhelpers.web.routers.task_helpers.responses import (
    asset_card,
    job_assets,
    job_inputs,
    job_status_payload,
    reference_asset,
)
from ofmhelpers.web.routers.task_helpers.routes import add_job_routes
from ofmhelpers.web.routers.task_helpers.serving import media_response, serve_job_file
from ofmhelpers.web.routers.task_helpers.uploads import (
    AUDIO_EXTS,
    IMAGE_EXTS,
    IMAGE_KINDS,
    MEDIA_KINDS,
    VIDEO_EXTS,
    VIDEO_KINDS,
    assets_root,
    assets_root_for,
    assets_root_for_actor,
    assets_root_for_user,
    classify_kind,
    convert_heic_upload,
    in_asset_root,
    make_job_dir,
    register_generated_asset,
    register_grouped_results,
    require_upload_kind,
    safe_filename,
    save_asset,
    save_upload,
    strip_asset_hash_prefix,
    uploads_root,
)

__all__ = [
    "AUDIO_EXTS",
    "IMAGE_EXTS",
    "IMAGE_KINDS",
    "MEDIA_KINDS",
    "VIDEO_EXTS",
    "VIDEO_KINDS",
    "add_job_routes",
    "asset_card",
    "assets_root",
    "assets_root_for",
    "assets_root_for_actor",
    "assets_root_for_user",
    "build_ordered_paths",
    "classify_kind",
    "convert_heic_upload",
    "download_name",
    "in_asset_root",
    "job_assets",
    "job_inputs",
    "job_status_payload",
    "keep_files",
    "keep_videos",
    "make_job_dir",
    "media_response",
    "reference_asset",
    "register_generated_asset",
    "register_grouped_results",
    "require_upload_kind",
    "resolve_existing_ref",
    "safe_filename",
    "save_asset",
    "save_upload",
    "serve_job_file",
    "strip_asset_hash_prefix",
    "uploads_root",
]
