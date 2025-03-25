from pydantic import BaseModel


class InstrumentCreate(BaseModel):
    name: str
    ticker: str