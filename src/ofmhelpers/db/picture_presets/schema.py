# Copyright (C) 2026 jpkrajewski

from pydantic import BaseModel


class PicturePreset(BaseModel):
    id: int
    owner_id: int
    name: str
    prompt: str
    images: list[str]
    aspect_ratio: str | None = None
    mine: bool = False
