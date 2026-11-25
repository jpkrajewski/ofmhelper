# Copyright (C) 2026 jpkrajewski

import logging
import os
import shutil
import time
from pathlib import Path

from pydantic import BaseModel, Field

from ofmhelpers.features.server.models import Folder, Host, Usage

logger = logging.getLogger(__name__)

PROC = Path("/proc")


def _read(name: str) -> str:
    try:
        return (PROC / name).read_text()
    except OSError:  # silent: no /proc outside Linux: reads 0
        return ""


_IDLE, _IOWAIT = 3, 4


class CpuTicks(BaseModel, frozen=True):
    busy: int
    total: int


class MemInfo(BaseModel):
    total: int = Field(0, alias="MemTotal")
    available: int | None = Field(None, alias="MemAvailable")
    swap_total: int = Field(0, alias="SwapTotal")
    swap_free: int | None = Field(None, alias="SwapFree")


class FolderSize(BaseModel, frozen=True):
    bytes: int
    files: int


def _cpu_ticks() -> CpuTicks:
    for line in _read("stat").splitlines():
        if line.startswith("cpu "):
            values = [int(v) for v in line.split()[1:]]
            idle = values[_IDLE] + (values[_IOWAIT] if len(values) > _IOWAIT else 0)
            total = sum(values)
            return CpuTicks(busy=total - idle, total=total)
    return CpuTicks(busy=0, total=0)


def cpu_percent(sample_s: float) -> float:
    first = _cpu_ticks()
    time.sleep(sample_s)
    second = _cpu_ticks()
    spent = second.total - first.total
    return 100 * (second.busy - first.busy) / spent if spent > 0 else 0.0


def _meminfo() -> MemInfo:
    found: dict[str, int] = {}
    for line in _read("meminfo").splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            found[key] = int(parts[0]) * 1024
    return MemInfo.model_validate(found)


def _load() -> list[float]:
    parts = _read("loadavg").split()
    if len(parts) < 3:  # noqa: PLR2004
        return [0.0, 0.0, 0.0]
    return [float(parts[0]), float(parts[1]), float(parts[2])]


def _existing(path: Path) -> Path:
    path = path.absolute()
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def host(disk_path: Path, sample_s: float) -> Host:
    mem = _meminfo()
    total = mem.total
    swap_total = mem.swap_total
    disk = shutil.disk_usage(_existing(disk_path))
    uptime = _read("uptime").split()
    return Host(
        cpus=os.cpu_count() or 1,
        cpu_percent=cpu_percent(sample_s),
        load=_load(),
        memory=Usage(
            used=total - (total if mem.available is None else mem.available),
            total=total,
        ),
        swap=Usage(
            used=swap_total - (swap_total if mem.swap_free is None else mem.swap_free),
            total=swap_total,
        ),
        disk=Usage(used=disk.total - disk.free, total=disk.total),
        uptime_s=float(uptime[0]) if uptime else 0.0,
    )


def _size(path: Path) -> FolderSize:
    size = files = 0
    for dirpath, _dirs, names in os.walk(path):
        for name in names:
            try:
                size += os.lstat(os.path.join(dirpath, name)).st_size  # noqa: PTH118
            except OSError:  # silent: gone mid-walk
                continue
            files += 1
    return FolderSize(bytes=size, files=files)


def folders(root: Path) -> list[Folder]:
    found: list[Folder] = []
    loose_bytes = loose_files = 0
    try:
        entries = list(os.scandir(root))
    except OSError:
        logger.warning("server: %s not listed", root, exc_info=True)
        return []
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                folder = _size(Path(entry.path))
                if folder.bytes:
                    found.append(
                        Folder(name=entry.name, bytes=folder.bytes, files=folder.files),
                    )
            elif entry.is_file(follow_symlinks=False):
                loose_bytes += entry.stat(follow_symlinks=False).st_size
                loose_files += 1
        except OSError:  # silent: gone mid-walk
            continue
    if loose_files:
        found.append(Folder(name="(files)", bytes=loose_bytes, files=loose_files))
    return sorted(found, key=lambda f: f.bytes, reverse=True)
