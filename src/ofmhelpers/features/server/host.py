"""The machine's numbers from Linux's /proc and the disk, stdlib only.

Inside a container /proc/stat, /proc/meminfo, /proc/loadavg and
/proc/uptime are the host's (Docker shows them as they are), and the
uploads root is a bind mount of the host's disk: the numbers are the box's,
not the container's. Outside Linux (a Windows host running the tests) a
file is missing and its numbers read 0.
"""

import os
import shutil
import time
from pathlib import Path

from ofmhelpers.features.server.models import Folder, Host, Usage

PROC = Path("/proc")


def _read(name: str) -> str:
    try:
        return (PROC / name).read_text()
    except OSError:
        return ""


# /proc/stat's "cpu" line: user nice system idle iowait irq ...
_IDLE, _IOWAIT = 3, 4


def _cpu_ticks() -> tuple[int, int]:
    """(busy, total) jiffies since boot, all CPUs; waiting on the disk
    counts as idle."""
    for line in _read("stat").splitlines():
        if line.startswith("cpu "):
            values = [int(v) for v in line.split()[1:]]
            idle = values[_IDLE] + (values[_IOWAIT] if len(values) > _IOWAIT else 0)
            total = sum(values)
            return total - idle, total
    return 0, 0


def cpu_percent(sample_s: float) -> float:
    """How busy all CPUs were over the next `sample_s` seconds."""
    busy0, total0 = _cpu_ticks()
    time.sleep(sample_s)
    busy1, total1 = _cpu_ticks()
    spent = total1 - total0
    return 100 * (busy1 - busy0) / spent if spent > 0 else 0.0


def _meminfo() -> dict[str, int]:
    """/proc/meminfo in bytes."""
    found: dict[str, int] = {}
    for line in _read("meminfo").splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            found[key] = int(parts[0]) * 1024
    return found


def _load() -> tuple[float, float, float]:
    parts = _read("loadavg").split()
    if len(parts) < 3:  # noqa: PLR2004 -- 1, 5, 15 minutes
        return 0.0, 0.0, 0.0
    return float(parts[0]), float(parts[1]), float(parts[2])


def _existing(path: Path) -> Path:
    """`path`, or its nearest folder that exists (a fresh server has no
    uploads folder yet: the disk it would be on)."""
    path = path.absolute()
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def host(disk_path: Path, sample_s: float) -> Host:
    mem = _meminfo()
    total = mem.get("MemTotal", 0)
    swap_total = mem.get("SwapTotal", 0)
    disk = shutil.disk_usage(_existing(disk_path))
    uptime = _read("uptime").split()
    return Host(
        cpus=os.cpu_count() or 1,
        cpu_percent=cpu_percent(sample_s),
        load=_load(),
        memory=Usage(used=total - mem.get("MemAvailable", total), total=total),
        swap=Usage(used=swap_total - mem.get("SwapFree", swap_total), total=swap_total),
        disk=Usage(used=disk.total - disk.free, total=disk.total),
        uptime_s=float(uptime[0]) if uptime else 0.0,
    )


def _size(path: Path) -> tuple[int, int]:
    """(bytes, files) under `path`, links not followed; a file gone mid-walk
    is skipped."""
    size = files = 0
    for dirpath, _dirs, names in os.walk(path):
        for name in names:
            try:
                size += os.lstat(os.path.join(dirpath, name)).st_size  # noqa: PTH118
            except OSError:
                continue
            files += 1
    return size, files


def folders(root: Path) -> list[Folder]:
    """Every top-level folder of `root` holding anything, biggest first; its
    loose files as one row named "(files)"."""
    found: list[Folder] = []
    loose_bytes = loose_files = 0
    try:
        entries = list(os.scandir(root))
    except OSError:
        return []
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                size, files = _size(Path(entry.path))
                if size:
                    found.append(Folder(name=entry.name, bytes=size, files=files))
            elif entry.is_file(follow_symlinks=False):
                loose_bytes += entry.stat(follow_symlinks=False).st_size
                loose_files += 1
        except OSError:
            continue
    if loose_files:
        found.append(Folder(name="(files)", bytes=loose_bytes, files=loose_files))
    return sorted(found, key=lambda f: f.bytes, reverse=True)
