from sqlalchemy import select, insert
from sqlalchemy.exc import IntegrityError

from src.config import settings
from src.db.db import async_session_maker
from src.db.users import usersManager
from src.models import Instruments
from src.models.users import RoleEnum, Users

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

async def create_admin_user():
    async with async_session_maker() as session:
        result = await session.execute(
            select(Users).where(Users.role == RoleEnum.ADMIN, Users.api_key == settings.ADMIN_API_KEY)
        )
        existing_admin = result.scalar_one_or_none()
        if existing_admin:
            existing_admin.is_active = True
            await session.commit()
            print("Админ уже существует, создание не требуется.")
            return

        await usersManager.create(session, {
            'name': 'admin',
            'role': RoleEnum.ADMIN,
            'api_key': settings.ADMIN_API_KEY
        })
