import json

from fastapi import HTTPException, status

from src.db.instrumentManager import instrumentsManager
from src.logger import cache_logger
from src.redis_conn import redis_client
from src.utils.custom_serializer import custom_serializer_json


async def update_instruments_cache(instruments):
    try:
        redis = await redis_client.get_redis()

        # Сначала удаляем старые данные (опционально)
        await redis.delete("instruments")

        pipe = redis.pipeline()

        for instrument in instruments:
            instrument_data = {
                "ticker": instrument.ticker,
                "name": instrument.name,
                'id': instrument.id,
            }
            pipe.hset("instruments", instrument.ticker, json.dumps(instrument_data))

        pipe.expire("instruments", 420)
        await pipe.execute()

    except Exception as e:
        print(f"Error updating cache: {e}")


async def update_cache_after_delete(ticker: str, request_id):
    try:
        redis = await redis_client.get_redis()

        await redis.hdel("instruments", ticker)

        await redis.expire("instruments", 420)
        cache_logger.info(f"[{request_id}] delete instrument", extra={'ticker': ticker})

    except Exception as e:
        cache_logger.error(f"[{request_id}] delete instrument error", exc_info=e, extra={'ticker': ticker})


async def load_user_redis(api_key, user, request_id):
    try:
        data_user_redis = {
            "id": user.uuid,
            "name": user.name,
            "role": user.role,
            "is_active": user.is_active,
        }

        redis = await redis_client.get_redis()
        await redis.set(f'user_key:{api_key}', json.dumps(data_user_redis, default=custom_serializer_json), ex=3600)

        cache_logger.info(f"[{request_id}] load user redis", extra={'user_id': str(user.uuid)})
        return data_user_redis
    except Exception as e:
        cache_logger.error(f"[{request_id}] load user redis error", extra={'user_id': str(user.uuid)})
        raise


async def clear_instruments_cache(request_id):
    try:
        redis = await redis_client.get_redis()
        await redis.delete("instruments")
        cache_logger.info(
            f"[{request_id}] Delete instruments"
        )
    except Exception as e:
        cache_logger.error(
            f"[{request_id}] ERROR Delete instruments", exc_info=e
        )


async def clear_user_cache(api_key, request_id):
    try:
        redis = await redis_client.get_redis()
        await redis.delete(f'user_key:{api_key}')
        cache_logger.info(
            f"[{request_id}] Delete user cache",
            extra={'api_key': api_key[:-10]}
        )
    except Exception as e:
        cache_logger.error(
            f"[{request_id}]ERROR Delete user", exc_info=e
        )


async def check_ticker_exists(ticker, session) -> int:
    redis = await redis_client.get_redis()

    # Проверка, есть ли кеш
    has_cache = await redis.exists("instruments")
    if not has_cache:
        instruments = await instrumentsManager.get_all(session)
        if not instruments:
            raise HTTPException(status_code=404, detail="No instruments found in DB")

        pipe = redis.pipeline()
        for instrument in instruments:
            instrument_data = {
                "ticker": instrument.ticker,
                "name": instrument.name,
                "id": instrument.id
            }
            pipe.hset("instruments", instrument.ticker, json.dumps(instrument_data))
        pipe.expire("instruments", 420)
        await pipe.execute()

    # Получение данных по конкретному тикеру
    instrument_data = await redis.hget("instruments", ticker)
    if not instrument_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ticker not found")

    try:
        instrument = json.loads(instrument_data)
        return instrument["id"]
    except (json.JSONDecodeError, KeyError):
        raise HTTPException(status_code=500, detail="Instrument data corrupted")


async def calculate_order_cost(
        r,
        ticker: str,
        quantity: float,
        side: str,  # 'BUY' или 'SELL'
):
    orderbook_key = f"orderbook:{ticker}:{'asks' if side == 'BUY' else 'bids'}"

    if side == 'BUY':
        orders = await r.zrange(orderbook_key, 0, -1, withscores=True)
    else:
        orders = await r.zrevrange(orderbook_key, 0, -1, withscores=True)

    remaining_qty = quantity
    total_cost = 0.0
    matched_orders = []

    for order_data, price in orders:

        _, order_qty, uuid_orders = order_data.split(":")
        order_qty = float(order_qty)

        qty_to_take = min(remaining_qty, order_qty)
        cost = qty_to_take * price

        matched_orders.append({
            "price": price,
            "quantity": qty_to_take,
            "cost": cost,
            "uuid": uuid_orders,
            "original_qty": order_qty
        })

        total_cost += cost
        remaining_qty -= qty_to_take

        if remaining_qty <= 0:
            break

    if remaining_qty > 0:
        raise ValueError(f"Недостаточно ликвидности. Осталось неисполненных: {remaining_qty}")
    return total_cost, matched_orders


async def match_limit_order(
        r,
        ticker: str,
        quantity: float,
        price_limit: float,
        side: str  # 'BUY' or 'SELL'
):
    orderbook_key = f"orderbook:{ticker}:{'asks' if side == 'BUY' else 'bids'}"

    if side == 'BUY':
        # asks сортируются от низкой к высокой, берём те, что <= limit
        orders = await r.zrangebyscore(orderbook_key, '-inf', price_limit, withscores=True)
    else:
        # bids от высокой к низкой, берём те, что >= limit
        orders = await r.zrevrangebyscore(orderbook_key, '+inf', price_limit, withscores=True)

    remaining_qty = quantity
    total_cost = 0.0
    matched_orders = []

    for order_data, price in orders:
        _, order_qty, order_uuid = order_data.split(":")
        order_qty = float(order_qty)

        qty_to_take = min(remaining_qty, order_qty)
        cost = qty_to_take * price

        matched_orders.append({
            "price": price,
            "quantity": qty_to_take,
            "cost": cost,
            "uuid": order_uuid,
            "original_qty": order_qty
        })

        total_cost += cost
        remaining_qty -= qty_to_take

        if remaining_qty <= 0:
            break

    return total_cost, matched_orders, remaining_qty
