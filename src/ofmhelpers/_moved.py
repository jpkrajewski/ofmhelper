"""Old import paths of modules that moved to their category folder. An old
path resolves to the very same module object, so what still names it keeps
working: RQ jobs queued before a deploy (RQ stores the function's path) and the
test suite (frozen during the 2026-11 cleanup; 339 patches name these modules).
New code imports the new paths. A row goes once nothing uses it."""

import importlib
import importlib.abc
import importlib.util
import sys
from importlib.machinery import ModuleSpec
from types import ModuleType

MOVED = {
    "ofmhelpers.aigenproviders": "ofmhelpers.integrations.ai_providers",
    "ofmhelpers.aigenproviders.kaiai": "ofmhelpers.integrations.ai_providers.kie",
    "ofmhelpers.apify": "ofmhelpers.integrations.apify",
    "ofmhelpers.clone_ai_check": "ofmhelpers.features.clone_links.ai_check",
    "ofmhelpers.downloaders": "ofmhelpers.integrations.downloaders",
    "ofmhelpers.exports": "ofmhelpers.media.exports",
    "ofmhelpers.gdrive": "ofmhelpers.integrations.gdrive",
    "ofmhelpers.instagram": "ofmhelpers.integrations.instagram",
    "ofmhelpers.iphones": "ofmhelpers.media.recorder",
    "ofmhelpers.links": "ofmhelpers.integrations.short_links",
    "ofmhelpers.llm": "ofmhelpers.integrations.llm",
    "ofmhelpers.phones": "ofmhelpers.features.phones.catalog",
    "ofmhelpers.repurpose": "ofmhelpers.media.repurpose",
    "ofmhelpers.signin": "ofmhelpers.integrations.signin",
    "ofmhelpers.socials": "ofmhelpers.integrations.socials",
    "ofmhelpers.trials": "ofmhelpers.features.trials",
    "ofmhelpers.utils.capcut": "ofmhelpers.media.capcut",
    "ofmhelpers.utils.character_sheet": "ofmhelpers.media.character_sheet",
    "ofmhelpers.utils.fake_media": "ofmhelpers.media.fake_media",
    "ofmhelpers.utils.ffmpeg": "ofmhelpers.media.ffmpeg",
    "ofmhelpers.utils.hdr": "ofmhelpers.media.hdr",
    "ofmhelpers.utils.heic": "ofmhelpers.media.heic",
    "ofmhelpers.utils.image_fit": "ofmhelpers.media.image_fit",
    "ofmhelpers.utils.metadata_cleaner": "ofmhelpers.media.metadata_cleaner",
    "ofmhelpers.utils.mov_atoms": "ofmhelpers.media.mov_atoms",
    "ofmhelpers.utils.uniqify": "ofmhelpers.media.uniqify",
    "ofmhelpers.utils.video_mp4": "ofmhelpers.media.video_mp4",
    "ofmhelpers.utils.video_probe": "ofmhelpers.media.video_probe",
    "ofmhelpers.viral": "ofmhelpers.features.viral",
    "ofmhelpers.web.account_removal": "ofmhelpers.features.accounts.removal",
    "ofmhelpers.web.account_tracker": "ofmhelpers.features.accounts.tracker",
    "ofmhelpers.web.clone_links": "ofmhelpers.features.clone_links",
    "ofmhelpers.web.clone_long": "ofmhelpers.features.clone_long",
    "ofmhelpers.web.db": "ofmhelpers.db",
    "ofmhelpers.web.download_names": "ofmhelpers.features.assets.names",
    "ofmhelpers.web.drive_folders": "ofmhelpers.features.drive.folders",
    "ofmhelpers.web.drive_space": "ofmhelpers.features.drive.space",
    "ofmhelpers.web.drive_uploads": "ofmhelpers.features.drive.uploads",
    "ofmhelpers.web.link_sync": "ofmhelpers.features.links.sync",
    "ofmhelpers.web.media_kinds": "ofmhelpers.features.assets.kinds",
    "ofmhelpers.web.phones_workbook": "ofmhelpers.features.phones.workbook",
    "ofmhelpers.web.posting": "ofmhelpers.features.drive.posting",
    "ofmhelpers.web.preview_backfill": "ofmhelpers.features.previews.backfill",
    "ofmhelpers.web.previews": "ofmhelpers.features.previews.render",
    "ofmhelpers.web.recovery": "ofmhelpers.features.jobs.recovery",
    "ofmhelpers.web.reel_thumbs": "ofmhelpers.features.reels.thumbs",
    "ofmhelpers.web.reel_watch": "ofmhelpers.features.reels.watch",
    "ofmhelpers.web.ref_usage": "ofmhelpers.features.assets.ref_usage",
    "ofmhelpers.web.schemas": "ofmhelpers.schemas",
    "ofmhelpers.web.stores": "ofmhelpers.db.stores",
    "ofmhelpers.web.sweeps": "ofmhelpers.features.jobs.sweeps",
}
_LONGEST_FIRST = sorted(MOVED, key=len, reverse=True)


def _new_name(name: str) -> str | None:
    for old in _LONGEST_FIRST:
        if name == old or name.startswith(old + "."):
            return MOVED[old] + name[len(old) :]
    return None


class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None) -> ModuleSpec | None:  # noqa: ARG002 -- MetaPathFinder's signature
        if _new_name(fullname) is None:
            return None
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec: ModuleSpec) -> ModuleType:
        new = _new_name(spec.name)
        assert new is not None  # noqa: S101 -- find_spec only hands over moved names
        module = importlib.import_module(new)
        # Import sets `__spec__` to the old name's: put the real one back after.
        self._real_spec = module.__spec__
        # Submodules it already loaded count as imported under the old path too:
        # importing one again would overwrite a same-named attribute of the
        # package (`socials.platforms`, a function and a subpackage).
        for name, child in list(sys.modules.items()):
            if name.startswith(new + "."):
                sys.modules.setdefault(spec.name + name[len(new) :], child)
        return module

    def exec_module(self, module: ModuleType) -> None:
        module.__spec__ = self._real_spec


def install() -> None:
    if not any(isinstance(finder, _Finder) for finder in sys.meta_path):
        sys.meta_path.insert(0, _Finder())
