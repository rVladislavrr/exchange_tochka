from http.client import HTTPException

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.db import get_async_session
from src.logger import api_logger
from src.models import UserBalances, Users

router = APIRouter(tags=["balance"], prefix='/balance')

@router.get('')
async def get_balance(request: Request,
                      session: AsyncSession = Depends(get_async_session)):

    request_id = request.state.request_id
    try:
        user_id = request.state.user.id

        result = await session.execute(
            select(Users)
            .options(selectinload(Users.balances).selectinload(UserBalances.instrument))
            .where(Users.uuid == user_id)
        )
        user = result.scalar_one()
        result = {
            item.instrument.ticker: item.available_balance + item.frozen_balance
            for item in user.balances if item.instrument.is_active
        }
        api_logger.info(
            "Get balance",
            extra={
                'user_id': str(user_id),
            }
        )

        return result
    except Exception as e:
        api_logger.error(
            f'[{request_id}] get balance failed',
            exc_info=e,
        )
        raise HTTPException(500)
