from fastapi import APIRouter, Request, Depends, BackgroundTasks, HTTPException
from pydantic import UUID4
from sqlalchemy.ext.asyncio import AsyncSession

from src import schemas
from src.db.db import get_async_session
from src.db.instrumentManager import instrumentsManager
from src.db.users import usersManager
from src.redis_conn import redis_client
from src.schemas import InstrumentCreate

router = APIRouter(tags=["Admin"], prefix='/admin')

async def clear_instruments_cache():
    redis = await redis_client.get_redis()
    await redis.delete("instruments")

async def clear_user_cache(api_key):
    redis = await redis_client.get_redis()
    await redis.delete(f'user_key:{api_key}')


@router.post('/instrument')
async def add_instrument(instrument: InstrumentCreate,
                         backgroundTasks: BackgroundTasks,
                         session: AsyncSession = Depends(get_async_session)):
    instrument = await instrumentsManager.create(session, dict(instrument))
    backgroundTasks.add_task(clear_instruments_cache)
    return instrument

@router.delete('/user/{user_id}')
async def delete_user(user_id: UUID4,
                      backgroundTasks: BackgroundTasks,
                      session: AsyncSession = Depends(get_async_session)) -> schemas.UserRegister:
    if user := await usersManager.get_user_uuid(user_id, session):

        if user.role.value == "admin":
            raise HTTPException(status_code=403, detail="FORBIDDEN, you cant disable admin")

        if not user.is_active:
            raise HTTPException(status_code=400, detail="User already deleted")

        user.is_active = False
        await session.commit()
        backgroundTasks.add_task(clear_user_cache, user.api_key)
        return user

    raise HTTPException(status_code=404, detail="User not found")