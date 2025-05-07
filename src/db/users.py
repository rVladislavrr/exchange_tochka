from sqlalchemy import select
from .base import BaseManager
from src.models import Users


class UsersManager(BaseManager):
    model = Users

    async def get_user_apikey(self, apikey, session) -> Users | None:
        query = select(self.model).where(self.model.api_key == apikey)
        return (await session.execute(query)).scalar()

    async def get_user_uuid(self, user_id, session) -> Users | None:
        return await session.get(self.model, user_id)


usersManager = UsersManager()
