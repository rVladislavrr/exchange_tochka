from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict, UUID4

from src.models.orders import SideEnum, StatusEnum

def create_GetOrder(orderOrm):
    body = Body.model_validate(orderOrm, from_attributes=True)
    order = GetOrder.model_validate(
        {
            **orderOrm.__dict__,
            "body": body
        },
        from_attributes=True
    )
    return order

class OrderBase(BaseModel):
    direction: SideEnum
    qty: int = Field(..., ge=1)
    ticker: str = Field(..., pattern='^[A-Z]{2,10}$')


class MarketOrder(OrderBase):
    model_config = ConfigDict(extra="forbid")


class LimitOrder(OrderBase):
    price: int = Field(..., gt=0)


class Body(BaseModel):
    direction: SideEnum = Field(validation_alias='side')
    ticker: str = Field(..., pattern='^[A-Z]{2,10}$')
    qty: int = Field(..., ge=1)
    price: int | None = None
    model_config = ConfigDict(
        exclude_none=True
    )


class GetOrder(BaseModel):
    id: UUID4 = Field(validation_alias='uuid')
    status: StatusEnum
    user_id: UUID4 = Field(validation_alias='user_uuid')
    timestamp: datetime = Field(validation_alias='create_at')
    body: Body
    filled: int | None = Field(None, ge=0)
    model_config = ConfigDict(
        exclude_none=True
    )