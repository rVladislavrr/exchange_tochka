from datetime import datetime

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Path, status
from pydantic import UUID4, BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from src import schemas
from src.db.db import get_async_session
from src.db.instrumentManager import instrumentsManager
from src.db.users import usersManager
from src.models import Instruments, Users, UserBalances, TradeLog
from src.schemas import InstrumentCreate
from src.schemas.deposit import Deposit
from src.utils.redis_utils import update_cache_after_delete, clear_instruments_cache, clear_user_cache

router = APIRouter(tags=["Admin"], prefix='/admin')


class BaseAnswer(BaseModel):
    success: bool = True


@router.post('/instrument')
async def add_instrument(instrument: InstrumentCreate,
                         backgroundTasks: BackgroundTasks,
                         session: AsyncSession = Depends(get_async_session)):
    instrument = await instrumentsManager.create(session, dict(instrument))
    backgroundTasks.add_task(clear_instruments_cache)
    return instrument


# TODO: При удалении пользователя все ордеры по нему должны быть отменены
@router.delete('/user/{user_id}')
async def delete_user(user_id: UUID4,
                      backgroundTasks: BackgroundTasks,
                      session: AsyncSession = Depends(get_async_session)) -> schemas.UserRegister:
    if user := await usersManager.get_user_uuid(user_id, session):

        if user.role.value == "ADMIN":
            raise HTTPException(status_code=403, detail="FORBIDDEN, you cant disable admin")

        if not user.is_active:
            raise HTTPException(status_code=400, detail="User already deleted")

        user.is_active = False
        user.delete_at = datetime.now()
        await session.commit()
        backgroundTasks.add_task(clear_user_cache, user.api_key)
        return user

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


# TODO: При удалении тикера все ордеры по нему должны быть отменены
@router.delete('/instrument/{ticker}')
async def delete_instrument(backgroundTasks: BackgroundTasks,
                            ticker: str = Path(pattern='^[A-Z]{2,10}$'),
                            session: AsyncSession = Depends(get_async_session)) -> BaseAnswer:
    if ticker == 'RUB':
        raise HTTPException(status_code=403, detail="Forbidden, you cant disable rub")
    await instrumentsManager.delete(ticker, session)
    backgroundTasks.add_task(update_cache_after_delete, ticker)
    return BaseAnswer()


@router.post('/balance/deposit')
async def deposit(deposit_obj: Deposit,
                  backgroundTasks: BackgroundTasks,
                  session: AsyncSession = Depends(get_async_session)) -> BaseAnswer:
    stmt = (
        select(Users, Instruments, UserBalances)
        .select_from(Users)
        .join(
            Instruments,
            and_(
                Instruments.ticker == deposit_obj.ticker,
                Instruments.is_active == True),
            isouter=True,
        )
        .join(
            UserBalances,
            and_(
                UserBalances.user_uuid == Users.uuid,
                UserBalances.instrument_id == Instruments.id,
            ),
            isouter=True,
        )
        .where(Users.uuid == deposit_obj.user_id)
        .limit(1)
    )

    result = await session.execute(stmt)
    user, instrument, user_balance = result.first() or (None, None, None)

    if not user:
        raise HTTPException(404, "User not found")
    if not instrument:
        raise HTTPException(404, "Instrument not found")

    try:
        if user_balance:
            user_balance.available_balance += deposit_obj.amount
        else:
            user_balance = UserBalances(
                user_uuid=user.uuid,
                instrument_id=instrument.id,
                available_balance=deposit_obj.amount,
                frozen_balance=0,
            )
            session.add(user_balance)

        # trade_log = TradeLog(
        #     buy_order_id=user.id,
        #     instrument_id=instrument.id,
        #     amount=deposit_obj.amount,
        #     type="deposit",
        # )
        # session.add(trade_log)

        await session.commit()
    except SQLAlchemyError as e:
        await session.rollback()
        print(e)
        raise HTTPException(500, "Transaction failed")

    return BaseAnswer()


@router.post('/balance/withdraw')
async def withdraw(deposit_obj: Deposit,
                   backgroundTasks: BackgroundTasks,
                   session: AsyncSession = Depends(get_async_session)) -> BaseAnswer:
    stmt = (
        select(UserBalances)
        .join(
            Instruments,
            Instruments.ticker == deposit_obj.ticker,
        )
        .where(UserBalances.user_uuid == deposit_obj.user_id, UserBalances.instrument_id == Instruments.id)
        .limit(1)
    )
    result = await session.execute(stmt)
    (userBalances,) = result.first() or (None,)
    if not userBalances:
        raise HTTPException(status_code=400, detail="Not enough balance or Not user or Not ticker")

    if userBalances.available_balance < deposit_obj.amount:
        raise HTTPException(status_code=400, detail="Not enough balance")
    try:
        userBalances.available_balance -= deposit_obj.amount
        if userBalances.available_balance == 0:
            await session.delete(userBalances)
        await session.commit()
    except SQLAlchemyError as e:
        await session.rollback()
        raise HTTPException(500, "Transaction failed")
    return BaseAnswer()
