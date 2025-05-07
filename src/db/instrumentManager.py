from fastapi import HTTPException
from typing import Any
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


instrumentsManager = InstrumentsManager()