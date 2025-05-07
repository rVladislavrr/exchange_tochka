import hashlib
import json
from datetime import datetime, timedelta
from typing import List

from sqlalchemy import select
from fastapi import APIRouter, status, Depends, BackgroundTasks

from src import schemas
from src.db.db import get_async_session, AsyncSession
from src.models import Instruments
from src.db.users import usersManager
from src.redis_conn import redis_client
from src.utils.custom_serializer import custom_serializer_json
from src.utils.redis_load import load_user_redis

router = APIRouter(tags=["Auth"], prefix='/public')


@router.post("/registration", status_code=status.HTTP_201_CREATED, )
async def registration(user: schemas.UserBase,
                       background_tasks: BackgroundTasks,
                       session: AsyncSession = Depends(get_async_session)) -> schemas.UserRegister:
    time = str(datetime.now()).encode()
    h = hashlib.shake_256(user.name.encode() + time)
    api_key = h.hexdigest(32)

    user = await usersManager.create(session, {'name': user.name,
                                               'api_key': api_key})

    background_tasks.add_task(load_user_redis, api_key, user)

    return schemas.UserRegister.model_validate(user, from_attributes=True)


async def update_instruments_cache(instruments: List[Instruments]):
    try:
        redis = await redis_client.get_redis()

        # Сначала удаляем старые данные (опционально)
        await redis.delete("instruments")

        pipe = redis.pipeline()

        for instrument in instruments:
            instrument_data = {
                "ticker": instrument.ticker,
                "name": instrument.name,
            }
            pipe.hset("instruments", instrument.ticker, json.dumps(instrument_data))

        pipe.expire("instruments", 420)
        await pipe.execute()

    except Exception as e:
        print(f"Error updating cache: {e}")
        # Лучше добавить логирование ошибок


@router.get('/instrument')
async def get_instruments(background_tasks: BackgroundTasks,
                          session=Depends(get_async_session)) -> list[schemas.InstrumentCreate]:
    try:
        redis = await redis_client.get_redis()

        if await redis.exists("instruments"):
            print("cache")
            all_instruments = await redis.hgetall("instruments")
            try:
                return [json.loads(value) for value in all_instruments.values()]
            except json.JSONDecodeError as e:
                print(f"Ошибка декодирования JSON из Redis: {e}")

        result = await session.execute(
            select(Instruments).where(Instruments.is_active == True)
        )
        instruments = result.scalars().all()
        if not instruments:
            return []

        background_tasks.add_task(update_instruments_cache, instruments)

        return instruments

    except Exception as e:
        print(f"Ошибка при получении инструментов: {e}")
        return []
