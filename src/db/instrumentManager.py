from fastapi import HTTPException
from typing import Any

from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .base import BaseManager
from src.models import Instruments
from .db import async_session_maker
from .userManager import usersManager
from ..logger import database_logger, cache_logger
from ..models.orders import StatusEnum, SideEnum
from ..redis_conn import redis_client


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
                'instrument_name': deleted_instrument.name,
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

    @staticmethod
    async def cancel_order_deleted_ticker(id_instrument, request_id):
        try:
            async with async_session_maker() as session:
                res = await session.execute(
                    select(Instruments).options(selectinload(Instruments.orders))
                    .where(
                        Instruments.id == id_instrument,
                    )
                )
                instruments = res.scalar_one_or_none()
                r = await redis_client.get_redis()
                pipe = r.pipeline()

                for order in instruments.orders:
                    if order.status == StatusEnum.EXECUTED or order.status == StatusEnum.CANCELLED:
                        continue
                    key = f"{int(order.price)}:{int(order.qty - order.filled)}:{order.uuid}:{round(order.create_at.timestamp(), 3)}"
                    orderbook_key = f"orderbook:{order.ticker}:{'asks' if order.side == SideEnum.SELL else 'bids'}"
                    order.status = StatusEnum.CANCELLED
                    pipe.zrem(orderbook_key, key)
                    pipe.hdel('active_orders', str(order.uuid))

                    cache_logger.info(
                        f"[{request_id}] cancel order (instrument)",
                        extra={"orderbook_key": orderbook_key, 'key': key}
                    )

                    if order.side == SideEnum.BUY:
                        userBalanceRUB = await usersManager.get_user_balance_by_ticker(
                            session, order.user_uuid, ticker='RUB', create_if_missing=True
                        )
                        userBalanceRUB.frozen_balance -= order.price * (order.qty - order.filled)
                        userBalanceRUB.available_balance += order.price * (order.qty - order.filled)
                        database_logger.info(
                            "Update balance(Cancel Order instrument)",
                            extra={
                                "user_id": str(order.user_uuid),
                                "ticker": order.ticker,
                                "available_balance +=": order.price * (order.qty - order.filled),
                                "frozen_balance -=": order.price * (order.qty - order.filled)
                            }
                        )
                    database_logger.info(
                        f"[{request_id}] Cancel Order ( instrument )",
                        extra={"id ": str(order.uuid), 'user_id': str(order.user_uuid),
                               "side": order.side.value,
                               "ticker": order.ticker,
                               "price": order.price}
                    )
                await pipe.execute()
                await session.commit()
                await session.close()
        except Exception as e:
            database_logger.error(
                f"[{request_id}] Cancel Order (DELETE instrument)",
                exc_info=e
            )
            cache_logger.info(
                f"[{request_id}] Cancel Order CACHE (DELETE instrument)",
                exc_info=e)
        finally:
            await session.close()


instrumentsManager = InstrumentsManager()
