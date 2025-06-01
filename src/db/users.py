from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .base import BaseManager
from src.models import Users, UserBalances
from ..logger import database_logger
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
        user = await super().create(session, data, request_id)
        instrument_id = await check_ticker_exists("RUB", session)
        userBalances = UserBalances(
            user_uuid=user.uuid,
            instrument_id=instrument_id,
            available_balance=0,
            frozen_balance=0,
        )
        session.add(userBalances)
        await session.commit()
        return user

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


usersManager = UsersManager()
