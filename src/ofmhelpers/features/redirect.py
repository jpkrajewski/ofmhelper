# Copyright (C) 2026 jpkrajewski

from pydantic import BaseModel


class Redirect(BaseModel):
    redirect: str
