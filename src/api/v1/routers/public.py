import hashlib
import json
from datetime import datetime

from sqlalchemy import select
from fastapi import APIRouter, status, Depends

from src import schemas
from src.db.db import get_async_session, AsyncSession
from src.models import Instruments
from src.db.users import usersManager
from src.redis_conn import redis_client
from src.utils.custom_serializer import custom_serializer_json

router = APIRouter(tags=["Auth"], prefix='/public')


@router.post("/registration", status_code=status.HTTP_201_CREATED)
async def registration(user: schemas.UserBase, session: AsyncSession = Depends(get_async_session)):
    time = str(datetime.now()).encode()
    h = hashlib.shake_256(user.name.encode() + time)
    api_key = h.hexdigest(32)

    user = await usersManager.create(session, {'name': user.name,
                                               'api_key': api_key})

    data_user = {
        "uuid": user.uuid,
        "name": user.name,
        "role": user.role,
        "balance": user.balance,
    }

    redis = await redis_client.get_redis()
    await redis.setex(f'user_key:{api_key}', redis_client.exp, json.dumps(data_user, default=custom_serializer_json))
    return user


@router.get('/instrument')
async def get_instruments(session=Depends(get_async_session)) -> list[schemas.InstrumentCreate]:
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
        pipe = redis.pipeline()
        for instrument in instruments:
            instrument_data = {"ticker": instrument.ticker, "name": instrument.name}
            pipe.hset("instruments", instrument.ticker, json.dumps(instrument_data))
        await pipe.execute()

        return instruments

    except Exception as e:
        print(f"Ошибка при получении инструментов: {e}")
        return []
