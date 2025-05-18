from sqlalchemy import select
from .base import BaseManager
from src.models import Users, UserBalances


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


usersManager = UsersManager()
