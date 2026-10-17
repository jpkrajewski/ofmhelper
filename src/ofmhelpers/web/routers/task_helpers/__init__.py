"""
Shared plumbing for every "upload files -> run in background -> download
result" router (clean-images, download-videos, kling3, nanobanana,
seedance, ...).

Each router should only need to define its own processing function and
wire it through these helpers -- everything else lives here once, split by
what it does:

- `uploads.py`    where files land, and what is allowed to land there
- `manifests.py`  reconciling a picker's new + reused entries into paths
- `responses.py`  the status/polling payloads every tool renders
- `serving.py`    handing a stored file back to the browser
- `routes.py`     the status page / polling / download endpoints of a job
- `download_names.py`  what a downloaded result is saved as

Import from this package, not the submodules: every tool router imports from
here, and which submodule a helper lives in is not their business.
"""

from ofmhelpers.web.routers.task_helpers.download_names import download_name
from ofmhelpers.web.routers.task_helpers.manifests import (
    build_ordered_paths,
    resolve_existing_ref,
    resolve_reference_uploads,
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
    ASSETS_ROOT,
    AUDIO_EXTS,
    IMAGE_EXTS,
    IMAGE_KINDS,
    MEDIA_KINDS,
    UPLOADS_ROOT,
    VIDEO_EXTS,
    VIDEO_KINDS,
    assets_root_for,
    classify_kind,
    convert_heic_upload,
    make_job_dir,
    register_generated_asset,
    register_grouped_results,
    require_upload_kind,
    safe_filename,
    save_asset,
    save_upload,
    strip_asset_hash_prefix,
)

__all__ = [
    "ASSETS_ROOT",
    "AUDIO_EXTS",
    "IMAGE_EXTS",
    "IMAGE_KINDS",
    "MEDIA_KINDS",
    "UPLOADS_ROOT",
    "VIDEO_EXTS",
    "VIDEO_KINDS",
    "add_job_routes",
    "asset_card",
    "assets_root_for",
    "build_ordered_paths",
    "classify_kind",
    "convert_heic_upload",
    "download_name",
    "job_assets",
    "job_inputs",
    "job_status_payload",
    "make_job_dir",
    "media_response",
    "reference_asset",
    "register_generated_asset",
    "register_grouped_results",
    "require_upload_kind",
    "resolve_existing_ref",
    "resolve_reference_uploads",
    "safe_filename",
    "save_asset",
    "save_upload",
    "serve_job_file",
    "strip_asset_hash_prefix",
]
