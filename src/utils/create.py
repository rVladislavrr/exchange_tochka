import uuid

from sqlalchemy import select, insert
from sqlalchemy.exc import IntegrityError

from src.config import settings
from src.db.db import async_session_maker
from src.db.users import usersManager
from src.models import Instruments
from src.models.users import RoleEnum, Users
from src.redis_conn import redis_client

RUB_TICKER = 'RUB'


async def create_rub():
    async with async_session_maker() as session:
        try:
            result = await session.execute(
                select(Instruments).where(
                    Instruments.ticker == RUB_TICKER,
                    Instruments.is_active == True
                )
            )
            rub = result.scalars().one_or_none()

            if not rub:
                await session.execute(
                    insert(Instruments).values(
                        name="Российский рубль",
                        ticker=RUB_TICKER,
                        is_active=True
                    )
                )
                await session.commit()
                print('Создан рубль')
            else:
                print('RUB уже существует, создание не требуется.')
        except IntegrityError:
            await session.rollback()
            print("RUB уже был создан другим воркером.")


ADMIN_LOCK_KEY = "create_admin_user_lock"
LOCK_TTL = 5

async def create_admin_user():
    r = await redis_client.get_redis()
    lock_acquired = await r.setnx(ADMIN_LOCK_KEY, "locked")
    if not lock_acquired:
        print("Другой воркер уже занимается созданием админа.")
        return
    await r.expire(ADMIN_LOCK_KEY, LOCK_TTL)

    try:
        async with async_session_maker() as session:
            result = await session.execute(
                select(Users).where(
                    Users.role == RoleEnum.ADMIN,
                    Users.api_key == settings.ADMIN_API_KEY
                )
            )
            existing_admin = result.scalars().first()
            if existing_admin:
                existing_admin.is_active = True
                await session.commit()
                print("Админ уже существует, создание не требуется.")
                return

            await usersManager.create_admin(session, {
                'name': 'admin',
                'role': RoleEnum.ADMIN,
                'api_key': settings.ADMIN_API_KEY
            }, request_id=uuid.uuid4())
            print("Создан новый админ.")
    except IntegrityError:
        await session.rollback()
        print("Админ уже создан другим воркером — всё ок.")
    finally:
        await r.delete(ADMIN_LOCK_KEY)  # снимаем блокировку