from pydantic import BaseModel, Field


class InstrumentCreate(BaseModel):
    name: str
    ticker: str = Field(pattern='^[A-Z]{2,10}$')

class InstrumentSchema(InstrumentCreate):
    id: int = Field(gt=0)