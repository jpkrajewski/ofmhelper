import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, TypeAdapter, field_validator

ADDONS_FILE = Path(__file__).with_name("addons.json")


class PromptAddon(BaseModel):
    key: str
    label: str
    text: str
    pattern: str
    check: bool = False

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        re.compile(value, re.IGNORECASE)
        return value


@lru_cache(maxsize=1)
def prompt_addons() -> list[PromptAddon]:
    return TypeAdapter(list[PromptAddon]).validate_json(ADDONS_FILE.read_bytes())
