from pydantic import BaseModel


class Redirect(BaseModel):
    redirect: str
