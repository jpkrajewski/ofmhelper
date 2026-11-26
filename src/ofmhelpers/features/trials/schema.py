# Copyright (C) 2026 jpkrajewski

from pydantic import BaseModel


class Made(BaseModel):
    generated: int
    failed: int
