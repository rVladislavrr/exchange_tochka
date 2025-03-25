import json

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
        if ("/public/" in request.url.path
                or request.url.path.endswith("/docs")
                or request.url.path.endswith("/openapi.json")):
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("TOKEN "):
            return JSONResponse({"detail": "Missing or invalid token"}, status_code=401)

        token = auth_header.split(" ")[1]

        if len(token) != 64:
            return JSONResponse({"detail": "Missing or invalid token"}, status_code=401)

        try:
            redis = await redis_client.get_redis()
            userJson = await validate_token(token, redis)
            user = json.loads(userJson)

        except Exception as e:
            print(e)
            return JSONResponse({"detail": "Missing or invalid token"}, status_code=401)

        if ('/admin/' in request.url.path
                and user.get("role", "user") != 'admin'):
            return JSONResponse({"detail": "FORBIDDEN"}, status_code=403)

        request.state.user = json.loads(user)
        return await call_next(request)
