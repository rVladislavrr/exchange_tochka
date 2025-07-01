import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Security, Depends
from fastapi.security import APIKeyHeader
from starlette.middleware.cors import CORSMiddleware

from src.middlewares.auth_middleware import AuthMiddleware
from src.middlewares.log_middleware import LoggingMiddleware
from src.redis_conn import redis_client
from src.api.v1 import router
from src.utils.create import create_rub, create_admin_user

api_key_header = APIKeyHeader(name="Authorization", auto_error=False, description=r"Форма записи TOKEN \<token\>")

async def for_documentation(api_key: str = Security(api_key_header)):
    pass

# TODO: при запуске обновлять кеш все ордеров
@asynccontextmanager
async def lifespan(app: FastAPI):
    for _ in range(5):
        try:
            await redis_client.connect()
            await create_rub()
            await create_admin_user()
            break
        except Exception as e:
            await asyncio.sleep(1)
            print(e)
    else:
        exit('Bad conection')
    yield
    await redis_client.close()
app = FastAPI(
    lifespan=lifespan,
    dependencies=[Depends(for_documentation)],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix='/api')
app.add_middleware(AuthMiddleware)
# старая версия
app.add_middleware(LoggingMiddleware)