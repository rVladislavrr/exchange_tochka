import hashlib
from datetime import datetime
from fastapi import APIRouter, status, Depends, BackgroundTasks

from src import schemas
from src.db.db import get_async_session, AsyncSession
from src.db.users import usersManager
from src.utils.get_resources import get_instruments
from src.utils.redis_utils import load_user_redis

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


@router.get('/instrument', name='get_instruments')
async def get_instruments_api(background_tasks: BackgroundTasks,
                              session=Depends(get_async_session)) -> list[schemas.InstrumentCreate]:

    instruments = await get_instruments(session, background_tasks)
    return instruments
