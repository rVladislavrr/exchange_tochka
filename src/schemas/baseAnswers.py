from pydantic import BaseModel


class BaseAnswer(BaseModel):
    success: bool = True
