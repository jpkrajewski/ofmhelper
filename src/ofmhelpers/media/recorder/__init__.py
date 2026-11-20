from functools import lru_cache
from importlib import resources

from ofmhelpers.media.recorder.models import DeviceProfile

__all__ = ["DeviceProfile", "available", "load_profile"]


def available() -> list[str]:
    root = resources.files(__package__)
    return sorted(
        p.name for p in root.iterdir() if p.is_dir() and (p / "profile.json").is_file()
    )


@lru_cache
def load_profile(name: str) -> DeviceProfile:
    if name not in available():
        msg = f"no iPhone profile named {name!r}"
        raise ValueError(msg)
    raw = resources.files(__package__).joinpath(name, "profile.json").read_text()
    return DeviceProfile.model_validate_json(raw)
