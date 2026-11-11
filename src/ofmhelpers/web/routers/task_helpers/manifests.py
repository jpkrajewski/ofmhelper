"""A picker's manifest (`features/assets/manifests.py`) as routes use it: a
forged or stale path is a 400."""

from ofmhelpers.features.assets import manifests
from ofmhelpers.features.assets.manifests import keep_files, keep_videos
from ofmhelpers.web.routers.task_helpers.uploads import refused_as_400

__all__ = ["build_ordered_paths", "keep_files", "keep_videos", "resolve_existing_ref"]

resolve_existing_ref = refused_as_400(manifests.resolve_existing_ref)
build_ordered_paths = refused_as_400(manifests.build_ordered_paths)
