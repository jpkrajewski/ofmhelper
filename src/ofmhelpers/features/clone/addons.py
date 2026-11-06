"""/clone's prompt add-ons: one-click endings for the prompt, from what the
clone prompts on prod added by hand (393 runs, 2026-11-06: same clothes as
the video 58, remove text overlay 55, no tattoos 41, clothes from @Image2 19,
reflections/screens 10, jewelry/piercings 7, face visible 7). The ones a
re-run of the same video and image most often added (`check`: clothes 7,
tattoos 4, jewelry 4, text overlay 3) are asked again in the Start dialog.

A new add-on = one entry in `addons.json`, in the order the page shows."""

import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, TypeAdapter, field_validator

ADDONS_FILE = Path(__file__).with_name("addons.json")


class PromptAddon(BaseModel):
    """One add-on button. `text` is what a click appends; `pattern` (a
    JavaScript and Python regex, matched case-insensitively) says the prompt
    already asks for it, in any wording the prompts used ("NO TATTOS")."""

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
