from fastapi import HTTPException
from typing import Any

from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .base import BaseManager
from src.models import Instruments


class InstrumentsManager(BaseManager):
    model = Instruments

    async def create(self, session: AsyncSession, data: dict) -> Any:
        try:
            return await super().create(session=session, data=data)

        except IntegrityError as error:
            # Улучшенная проверка нарушения уникальности
            if "unique constraint" in str(error).lower() or "duplicate key" in str(error).lower():
                ticker = data.get('ticker', 'unknown')
                raise HTTPException(
                    status_code=409,
                    detail=f"Instrument with ticker '{ticker}' already exists"
                ) from error

            # Для других IntegrityError
            raise HTTPException(
                status_code=400,
                detail="Database integrity error occurred"
            ) from error

        except Exception as error:
            raise HTTPException(
                status_code=500,
                detail="Internal server error"
            ) from error

    async def get_all(self, session: AsyncSession) -> Any:
        instruments = await session.execute(
            select(self.model).where(self.model.is_active == True)
        )
        return instruments.scalars().all()

    async def get_ticker(self, ticker: str, session: AsyncSession) -> Any:
        return (await session.execute(
            select(self.model).where(self.model.ticker == ticker)
        )).scalar_one_or_none()

    async def delete(self, ticker: str, session: AsyncSession):
        stmt = (
            update(self.model)
            .where(self.model.ticker == ticker, self.model.is_active == True)
            .values(is_active=False, delete_at=func.now())
            .returning(self.model)
        )

        result = await session.execute(stmt)
        deleted_instrument = result.scalar_one_or_none()
        await session.commit()

        if not deleted_instrument:
            raise HTTPException(
                status_code=404,
                detail=f"Instrument with ticker {ticker} not found or already inactive"
            )

        return deleted_instrument


instrumentsManager = InstrumentsManager()
