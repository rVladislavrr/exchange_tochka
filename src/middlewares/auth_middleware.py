from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.redis_conn import redis_client


async def validate_token(token, redis):
    user = await redis.get(f'user_key:{token}')
    if not user:
        return JSONResponse({"detail": "Missing or invalid token"}, status_code=401)
    return user


class AuthMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        if (request.url.path.endswith("/registration")
                or request.url.path.endswith("/login")
                or request.url.path.endswith("/docs")
                or request.url.path.endswith("/openapi.json")):
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Token "):
            return JSONResponse({"detail": "Missing or invalid token"}, status_code=401)

        token = auth_header.split(" ")[1]

        redis = await redis_client.get_redis()

        user = await validate_token(token, redis)

        request.state.user = user
        return await call_next(request)
