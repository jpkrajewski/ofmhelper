import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

_FILE = Path(__file__).with_name("retired.json")


class RetiredModule(BaseModel):
    area: str
    routers: list[str] = []
    tests: list[str] = []


class Retired(BaseModel):
    modules: list[RetiredModule]

    @property
    def areas(self) -> frozenset[str]:
        return frozenset(m.area for m in self.modules)

    def has(self, area: str) -> bool:
        return area in self.areas

    @property
    def routers(self) -> frozenset[str]:
        return frozenset(r for m in self.modules for r in m.routers)

    @property
    def tests(self) -> list[str]:
        return [t for m in self.modules for t in m.tests]


@lru_cache(maxsize=1)
def retired() -> Retired:
    return Retired.model_validate(json.loads(_FILE.read_text(encoding="utf-8")))
