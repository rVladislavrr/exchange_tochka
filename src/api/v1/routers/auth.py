import hashlib
import json
from datetime import datetime
from fastapi import APIRouter, status, Depends, Request, Security, HTTPException
from fastapi.security import APIKeyHeader

from src.db.db import get_async_session, AsyncSession
from src.schemas.user import UserBase, ProtectedRout
from src.db.users import usersManager
from src.redis_conn import redis_client
from src.utils.custom_serializer import custom_serializer_json

router = APIRouter(tags=["Auth"])
api_key_header = APIKeyHeader(name="Authorization", auto_error=False, description=r"Форма записи Token \<token\>")

async def for_documentation(api_key: str = Security(api_key_header)):
    pass


@router.post("/registration", status_code=status.HTTP_201_CREATED)
async def registration(user: UserBase, session: AsyncSession = Depends(get_async_session)):
    time = str(datetime.now()).encode()
    h = hashlib.shake_256(user.name.encode() + time)
    api_key = h.hexdigest(32)

    user = await usersManager.create(session, {'name': user.name,
                                               'api_key': api_key})

    data_user = {
        "uuid": user.uuid,
        "name": user.name,
        "role": user.role,
    }

    redis = await redis_client.get_redis()
    await redis.setex(f'user_key:{api_key}', redis_client.exp, json.dumps(data_user, default=custom_serializer_json))
    return user


@router.get("/protected_rout", status_code=status.HTTP_200_OK, dependencies=[Depends(for_documentation)])
async def protected_rout(request: Request) -> ProtectedRout:
    user = getattr(request.state, "user", None)
    if user:
        return ProtectedRout(**{"user": json.loads(user)})
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

