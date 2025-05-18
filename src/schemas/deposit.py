from pydantic import BaseModel, UUID4, Field


class Deposit(BaseModel):
    user_id: UUID4
    ticker: str = Field(pattern='^[A-Z]{2,10}$')
    amount: int = Field(gt=0)

class Withdraw(Deposit):
    pass