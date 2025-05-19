from sqlalchemy import select, insert
from sqlalchemy.exc import IntegrityError

from src.db.db import async_session_maker
from src.models import Instruments

RUB_TICKER = 'RUB'

async def create_rub():
    async with async_session_maker() as session:
        result = await session.execute(
            select(Instruments).where(Instruments.ticker == RUB_TICKER)
        )
        rub = result.scalar_one_or_none()

        if not rub:
            try:
                await session.execute(
                    insert(Instruments).values(
                        id=1,
                        name="Российский рубль",
                        ticker=RUB_TICKER,
                        is_active=True
                    )
                )
                await session.commit()
            except IntegrityError:
                await session.rollback()