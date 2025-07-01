from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, or_, and_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .base import BaseManager
from src.models import Users, UserBalances, Orders, Instruments
from .db import async_session_maker
from ..logger import database_logger, cache_logger, api_logger
from ..models.orders import StatusEnum, SideEnum
from ..redis_conn import redis_client
from ..schemas.baseAnswers import BaseAnswer
from ..utils.redis_utils import check_ticker_exists


class UsersManager(BaseManager):
    model = Users
    model_balance = UserBalances

    async def get_user_apikey(self, apikey, session) -> Users | None:
        query = select(self.model).where(self.model.api_key == apikey)
        return (await session.execute(query)).scalar()

    async def get_user_uuid(self, user_id, session) -> Users | None:
        return await session.get(self.model, user_id)

    async def update_balance(self, user_id, instrument_id, amount, session, frozen_balance=0) -> UserBalances:
        pass

    async def create(self, session: AsyncSession, data: dict, request_id) -> Any:
        try:
            user = await super().create(session, data, request_id)
            instrument_id = await check_ticker_exists("RUB", session)
            userBalances = UserBalances(
                user_uuid=user.uuid,
                instrument_id=instrument_id,
                available_balance=0,
                frozen_balance=0,
            )
            database_logger.info(f'[{request_id}] User registration', extra={"user_id":
                                                                                 str(user.uuid)})
            session.add(userBalances)
            await session.commit()
            return user
        except Exception as e:
            database_logger.error(f'[{request_id}] Bad registration', exc_info=e)
            raise

    async def create_admin(self, session: AsyncSession, data: dict, request_id) -> Any:
        try:
            user = await super().create(session, data, request_id)
            database_logger.info(f'[{request_id}] User registration', extra={"user_id":
                                                                 str(user.uuid)})
            return user
        except Exception as e:
            database_logger.error(f'[{request_id}] Bad registration', exc_info=e)
            raise

    @staticmethod
    async def get_user_balance_by_ticker(
            session: AsyncSession,
            user_uuid,
            ticker: str,
            create_if_missing: bool = False
    ) -> UserBalances | None:

        # Найти инструмент по тикеру
        instrument_id = await check_ticker_exists(ticker, session)

        balance_result = await session.execute(
            select(UserBalances)
            .where(
                UserBalances.user_uuid == user_uuid,
                UserBalances.instrument_id == instrument_id,

            )
        )
        balance = balance_result.scalars().first()

        if balance is None and create_if_missing:
            balance = UserBalances(
                user_uuid=user_uuid,
                instrument_id=instrument_id,
                available_balance=0.0,
                frozen_balance=0.0
            )
            session.add(balance)

        return balance

    @staticmethod
    async def cancel_order_deleted_user(user_id, request_id):
        try:
            async with async_session_maker() as session:
                res = await session.execute(select(Orders).options(selectinload(Orders.instrument)).where(
                    Orders.user_uuid == user_id,
                    or_(
                        Orders.status == StatusEnum.NEW,
                        Orders.status == StatusEnum.PARTIALLY_EXECUTED
                    )
                )
                )
                orders = res.scalars()
                r = await redis_client.get_redis()
                pipe = r.pipeline()

                for order in orders:
                    key = f"{int(order.price)}:{int(order.qty - order.filled)}:{order.uuid}:{round(order.create_at.timestamp(), 3)}"
                    orderbook_key = f"orderbook:{order.ticker}:{'asks' if order.side == SideEnum.SELL else 'bids'}"
                    pipe.zrem(orderbook_key, key)
                    pipe.hdel('active_orders', str(order.uuid))
                    old_status = order.status
                    order.status = StatusEnum.CANCELLED
                    database_logger.info(
                        f"[{request_id}] Cancel Order (user)",
                        extra={"id ": str(order.uuid), "old status": old_status.value, 'user_id': str(user_id),
                               "side": order.side.value,
                               "ticker": order.ticker,
                               "price": order.price}
                    )
                    cache_logger.info(
                        f"[{request_id}] Cancel Order  (user) cache ",
                        extra={"orderbook_key": orderbook_key, "key": key}
                    )

                await pipe.execute()
                await session.commit()
        except Exception as e:
            database_logger.error(
                f"[{request_id}] Cancel Order (DELETE USER)",
                exc_info=e
            )
            cache_logger.info(
                f"[{request_id}] Cancel Order CACHE (DELETE USER)",
                exc_info=e)
            raise HTTPException(500)

    @staticmethod
    async def deposit_user(session, deposit_obj, request_id):
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

        if deposit_obj.ticker == 'RUB':
            try:
                userBalanceRUB = await usersManager.get_user_balance_by_ticker(
                    session, deposit_obj.user_id, ticker='RUB', create_if_missing=True
                )
                userBalanceRUB.available_balance += deposit_obj.amount
                await session.commit()
                database_logger.info(
                    f"[{request_id}] Deposit",
                    extra={
                        'user': str(deposit_obj.user_id),
                        'ticker': deposit_obj.ticker,
                        'amount': deposit_obj.amount,
                    }
                )

            except Exception as e:
                database_logger.error(
                    f"[{request_id}] Cannot deposit",
                    exc_info=e
                )
            api_logger.info(
                f"[{request_id}] Deposit",
                extra={
                    'user': str(deposit_obj.user_id),
                    'ticker': deposit_obj.ticker,
                    'amount': deposit_obj.amount,
                }
            )
            await session.close()

            return BaseAnswer()

        if not instrument:
            await session.close()
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
            await session.commit()
            database_logger.info(
                f"[{request_id}] Deposit",
                extra={
                    'user': str(deposit_obj.user_id),
                    'ticker': deposit_obj.ticker,
                    'amount': deposit_obj.amount,
                }
            )
        except SQLAlchemyError as e:
            await session.rollback()
            raise e
        finally:
            await session.close()

    @staticmethod
    async def withdraw_user(session, deposit_obj, request_id):
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
            database_logger.info(
                f"[{request_id}] Withdraw",
                extra={
                    'user': str(deposit_obj.user_id),
                    'ticker': deposit_obj.ticker,
                    'amount': deposit_obj.amount,
                }
            )
            await session.commit()
        except SQLAlchemyError as e:
            await session.rollback()
            raise e
        finally:
            await session.close()


usersManager = UsersManager()
