import hashlib
import json
import secrets
from datetime import timezone
from http.client import HTTPException

from fastapi import APIRouter, status, Depends, BackgroundTasks, Path, Query, Request
from sqlalchemy import select

from src import schemas
from src.db.db import get_async_session, AsyncSession
from src.db.users import usersManager
from src.logger import api_logger, cache_logger
from src.models import TradeLog
from src.redis_conn import redis_client
from src.utils.get_resources import get_instruments
from src.utils.redis_utils import load_user_redis

router = APIRouter(tags=["Public"], prefix='/public')


def generate_api_key(username: str) -> str:
    random_part = secrets.token_hex(16)

    unique_string = f"{username}-{random_part}"

    return hashlib.sha256(unique_string.encode()).hexdigest()


@router.post("/register", status_code=status.HTTP_201_CREATED, )
async def registration(request: Request, user: schemas.UserBase,
                       background_tasks: BackgroundTasks,
                       session: AsyncSession = Depends(get_async_session)) -> schemas.UserRegister:
    request_id = request.state.request_id
    try:
        api_key = generate_api_key(user.name)
        user = await usersManager.create_admin(session, {'name': user.name,
                                                         'api_key': api_key}, request_id)

        background_tasks.add_task(load_user_redis, api_key, user, request_id)
        model = schemas.UserRegister.model_validate(user, from_attributes=True)

        api_logger.info(
            f'[{request_id}] User registered',
            extra={'user_id': str(model.id)}
        )

        return model

    except Exception as e:
        api_logger.error(
            f'[{request_id}] bad registration',
            exc_info=e
        )

        raise HTTPException(500)


@router.get('/instrument', name='get_instruments')
async def get_instruments_api(request: Request, background_tasks: BackgroundTasks,
                              session=Depends(get_async_session)) -> list[schemas.InstrumentCreate]:
    request_id = request.state.request_id
    try:
        instruments = await get_instruments(session, background_tasks)
        api_logger.info(
            f'[{request_id}] Get instruments',
        )
        return instruments
    except Exception as e:
        api_logger.error(
            f'[{request_id}] bad get instruments',
            exc_info=e
        )
        raise HTTPException(500)


@router.get('/transactions/{ticker}', name='get_instrument')
async def get_transaction(request: Request, ticker: str = Path(pattern='^[A-Z]{2,10}$'),
                          limit: int = Query(10, gt=0), session: AsyncSession = Depends(get_async_session)):
    request_id = request.state.request_id
    try:

        if limit < 199:
            r = await redis_client.get_redis()
            key = f"ticker:{ticker}"
            raw_data = await r.lrange(key, 0, limit - 1)
            api_logger.info(
                f'[{request_id}] get_transaction',
            )
            return [json.loads(tx) for tx in raw_data]
        else:
            res = (await session.execute(
                select(TradeLog).order_by(TradeLog.create_at).limit(limit)
            )).scalars()
            api_logger.info(
                f'[{request_id}] get_transaction',
            )
            return [{"ticker": item.ticker,
                     "amount": item.quantity,
                     "price": item.price,
                     "timestamp": item.create_at.replace(tzinfo=timezone.utc).isoformat()}
                    for item in res]
    except Exception as e:
        api_logger.error(
            f'[{request_id}] bad get_transaction',
            exc_info=e
        )
    finally:
        await session.close()


async def get_orderbook_levels(r, ticker: str, request_id, limit: int = 10):
    try:
        ask_key = f"orderbook:{ticker}:asks"
        bid_key = f"orderbook:{ticker}:bids"

        asks = await r.zrange(ask_key, 0, limit - 1, withscores=True)
        bids = await r.zrevrange(bid_key, 0, limit - 1, withscores=True)

        def format_orders(raw_orders):
            return [
                {
                    "price": price,
                    "qty": int(order_data.split(":")[1])
                }
                for order_data, price in raw_orders
            ]
        cache_logger.info(
            f'[{request_id}] get orderbook levels',
            extra={'ticker': ticker}
        )
        return {
            "ask_levels": format_orders(asks),
            "bid_levels": format_orders(bids),
        }
    except Exception as e:
        cache_logger.error(
            f'[{request_id}] bad get orderbook levels',
            exc_info=e
        )
        raise


@router.get('/orderbook/{ticker}')
async def get_orderbook(
        request: Request,
        ticker: str = Path(pattern='^[A-Z]{2,10}$'),
        limit: int = Query(10, gt=0),
):
    request_id = request.state.request_id
    try:
        r = await redis_client.get_redis()
        res = await get_orderbook_levels(r, ticker, limit=limit, request_id=request_id)
        api_logger.info(
            f'[{request_id}] get_orderbook',
            extra={'ticker': ticker}
        )
        return res
    except Exception as e:
        api_logger.error(
            f'[{request_id}] bad get_orderbook',
            exc_info=e
        )
        raise HTTPException(500)