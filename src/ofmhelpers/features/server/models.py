"""What /home's Server section shows (features/server/status.py fills it)."""

from pydantic import BaseModel

from ofmhelpers.cache.queue import QueueLoad


class Usage(BaseModel):
    """A used / total pair (bytes), with its share."""

    used: int
    total: int

    @property
    def percent(self) -> float:
        return 100 * self.used / self.total if self.total else 0.0


class Host(BaseModel):
    """The machine the app runs on, as Linux reports it (a container reads
    the host's /proc/stat, /proc/meminfo and /proc/loadavg)."""

    cpus: int
    cpu_percent: float
    load: tuple[float, float, float]
    memory: Usage
    swap: Usage
    disk: Usage
    uptime_s: float


class Folder(BaseModel):
    """One top-level folder of the uploads root."""

    name: str
    bytes: int
    files: int


class RunningJob(BaseModel):
    """An app job still running: what, whose, for how long."""

    task: str
    who: str
    running_s: float


class ServerStatus(BaseModel):
    host: Host
    folders: list[Folder]
    database_bytes: int | None
    redis_bytes: int | None
    queues: list[QueueLoad]
    jobs: list[RunningJob]
    # What is worth a look, worded for the page ("Disk 91% full").
    problems: list[str]
