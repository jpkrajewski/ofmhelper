"""iPhone recording profiles: one folder per model (`iphone17/`), each with
a `profile.json` decoded from real recordings and shipped with the package,
validated into `models.DeviceProfile`. Nothing to generate or run: adding a
model is adding a folder."""

from functools import lru_cache
from importlib import resources

from ofmhelpers.iphones.models import DeviceProfile

__all__ = ["DeviceProfile", "available", "load_profile"]


def available() -> list[str]:
    """The model folders that hold a profile."""
    root = resources.files(__package__)
    return sorted(
        p.name for p in root.iterdir() if p.is_dir() and (p / "profile.json").is_file()
    )


@lru_cache
def load_profile(name: str) -> DeviceProfile:
    """The profile in `iphones/<name>/profile.json`."""
    if name not in available():
        msg = f"no iPhone profile named {name!r}"
        raise ValueError(msg)
    raw = resources.files(__package__).joinpath(name, "profile.json").read_text()
    return DeviceProfile.model_validate_json(raw)
