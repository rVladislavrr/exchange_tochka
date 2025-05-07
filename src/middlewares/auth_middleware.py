import json

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.db.db import async_session_maker
from src.redis_conn import redis_client
from src.db.users import usersManager
from src.schemas.user import UserRedis
from src.utils.redis_utils import load_user_redis


async def validate_token(token):
    redis = await redis_client.get_redis()
    user = await redis.get(f'user_key:{token}')
    return user


class AuthMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        if ("/public/" in request.url.path
                or request.url.path.endswith("/docs")
                or request.url.path.endswith("/openapi.json")):
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("TOKEN "):
            return JSONResponse({"detail": "Missing or invalid token"}, status_code=401)

        token = auth_header.split(" ")[1]

        if len(token) != 64:
            print('/')
            return JSONResponse({"detail": "Missing or invalid token"}, status_code=401)

        try:
            if userJson := await validate_token(token):
                user = json.loads(userJson)
            else:
                async with async_session_maker() as session:
                    user = await usersManager.get_user_apikey(token, session)
                    if not user:
                        return JSONResponse({"detail": "Missing or invalid token"}, status_code=401)
                    else:
                        user = await load_user_redis(user.api_key, user)

        except Exception as e:
            print(e)
            return JSONResponse({"detail": "Missing or invalid token"}, status_code=401)

        user = UserRedis.model_validate(user, from_attributes=True)

        if not user.is_active:
            return JSONResponse({"detail": "User is not active"}, status_code=401)

        if ('/admin/' in request.url.path
                and user.role != 'admin'):
            return JSONResponse({"detail": "FORBIDDEN"}, status_code=403)

        request.state.user = user
        return await call_next(request)
