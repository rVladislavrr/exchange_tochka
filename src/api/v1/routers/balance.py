
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.db import get_async_session
from src.models import UserBalances, Users

router = APIRouter(tags=["balance"], prefix='/balance')

@router.get('')
async def get_balance(request: Request,
                      session: AsyncSession = Depends(get_async_session)):
    user_id = request.state.user.id

    result = await session.execute(
        select(Users)
        .options(selectinload(Users.balances).selectinload(UserBalances.instrument))
        .where(Users.uuid == user_id)
    )
    user = result.scalar_one()

    return {
        item.instrument.ticker: item.available_balance
        for item in user.balances
    }
