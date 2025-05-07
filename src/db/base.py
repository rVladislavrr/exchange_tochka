from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


class BaseManager:
    model: Any
    primary_key: str

    def __init__(self):
        if hasattr(self.model, "uuid"):
            self.primary_key = "uuid"
        else:
            self.primary_key = "id"

    async def create(self, session: AsyncSession, data: dict) -> Any:
        try:
            instance = self.model(**data)
            session.add(instance)
            await session.flush()
            await session.refresh(instance)
        except Exception as e:
            await session.rollback()
            raise e
        await session.commit()
        return instance
