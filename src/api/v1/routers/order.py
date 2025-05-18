import enum

from fastapi import APIRouter, Request, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.db import get_async_session
from src.models import UserBalances, Instruments


class Direction(enum.Enum):
    SELL: str = 'SELL'
    BUY: str = 'BUY'


class MarketOrder(BaseModel):
    direction: Direction
    qty: int = Field(..., ge=1)
    ticker: str = Field(pattern='^[A-Z]{2,10}$')


class LimitOrder(MarketOrder):
    price: int = Field(..., ge=0)


router = APIRouter(prefix='/orders', tags=['orders'])


@router.post('/')
async def create_router(order_data: MarketOrder | LimitOrder,
                        request: Request, session: AsyncSession = Depends(get_async_session)):
    if order_data.direction == Direction.SELL:

        userBalance: UserBalances | None = (await session.execute(
            select(UserBalances).join(Instruments, UserBalances.instrument_id == Instruments.id)
            .where(Instruments.ticker == order_data.ticker, UserBalances.user_uuid == request.state.user.id)
        )).scalars().one_or_none()

        if userBalance:
            if order_data.qty > userBalance.available_balance:
                raise
        else:
            raise

        print(userBalance)

    if isinstance(order_data, MarketOrder):
        pass
        # market
        # будет провека и сразу отмена если не возможно выполнить,

    else:
        pass
        # будет фоново класться если всё проходит проверку

        # limit
    pass
