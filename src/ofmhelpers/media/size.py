"""A picture's width and height in pixels, as one value."""

from pydantic import BaseModel


class Size(BaseModel, frozen=True):
    width: int
    height: int
