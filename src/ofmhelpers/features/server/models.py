from pydantic import BaseModel

from ofmhelpers.cache.queue import QueueLoad


class Usage(BaseModel):
    used: int
    total: int

    @property
    def percent(self) -> float:
        return 100 * self.used / self.total if self.total else 0.0


class Host(BaseModel):
    cpus: int
    cpu_percent: float
    load: list[float]
    memory: Usage
    swap: Usage
    disk: Usage
    uptime_s: float


class Folder(BaseModel):
    name: str
    bytes: int
    files: int


class RunningJob(BaseModel):
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
    problems: list[str]
