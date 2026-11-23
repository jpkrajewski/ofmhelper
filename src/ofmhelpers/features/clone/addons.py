# Copyright (C) 2026 jpkrajewski

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

ADDONS_FILE = Path(__file__).with_name("addons.json")


class PromptAddon(BaseModel):
    key: str
    label: str
    text: str
    check: bool = False


@lru_cache(maxsize=1)
def prompt_addons() -> list[PromptAddon]:
    return TypeAdapter(list[PromptAddon]).validate_json(ADDONS_FILE.read_bytes())
