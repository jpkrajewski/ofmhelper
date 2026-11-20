from pydantic import BaseModel


class Size(BaseModel, frozen=True):
    width: int
    height: int
