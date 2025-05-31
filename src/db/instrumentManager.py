from fastapi import HTTPException
from typing import Any

from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .base import BaseManager
from src.models import Instruments
from ..logger import database_logger


class InstrumentsManager(BaseManager):
    model = Instruments

    async def create(self, session: AsyncSession, data: dict, request_id) -> Any:
        try:
            res: Instruments = await super().create(session=session, data=data, request_id=request_id)

            database_logger.info(
                f"[{request_id}] Instrument create",
                extra={
                    "instrument_name": data.get('name'),
                    "ticker": data.get('ticker'),
                }
            )
            return res

        except IntegrityError as error:
            # Улучшенная проверка нарушения уникальности
            if "uq_active_ticker" in str(error).lower() or "duplicate key" in str(error).lower():
                ticker = data.get('ticker', 'unknown')
                database_logger.warning(
                    f"[{request_id}] Instrument NOT create (uq_active_ticker)",
                    extra={
                        "instrument_name": data.get('name'),
                        "ticker": data.get('ticker'),
                    }
                )
                raise HTTPException(
                    status_code=409,
                    detail=f"Instrument with ticker '{ticker}' already exists"
                ) from error

            # Для других IntegrityError
            database_logger.warning(
                f"[{request_id}] Database integrity error occurred (IntegrityError)",
            )
            raise HTTPException(
                status_code=400,
                detail="Database integrity error occurred"
            ) from error

        except Exception as error:
            database_logger.error(
                f"[{request_id}] Failed to create Instrument",
                exc_info=error,
                extra={
                    "instrument_name": data.get('name'),
                    "bucket": data.get('ticker'),
                    "error": str(error),
                }
            )
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

    async def delete(self, ticker: str, session: AsyncSession, request_id):
        try:
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
            database_logger.info(f"[{request_id}] Instrument deleted", extra={
                "ticker": ticker,
                "id": deleted_instrument.id,
                'instrument_name':  deleted_instrument.name,
            })

            return deleted_instrument
        except HTTPException as e:
            database_logger.warning(f"[{request_id}] Instrument Cannot deleted", extra={
                "ticker": ticker,
                "detail": e.detail,
            })
            raise
        except Exception as e:
            database_logger.error(
                f"[{request_id}] Failed to delete Instrument",
                exc_info=e,
            )
            raise HTTPException(500)


instrumentsManager = InstrumentsManager()
