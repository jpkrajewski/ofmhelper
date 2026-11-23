from pydantic import BaseModel


class Made(BaseModel):
    generated: int
    failed: int
