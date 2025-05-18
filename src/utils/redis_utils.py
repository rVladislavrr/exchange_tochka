import json

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
            }
            pipe.hset("instruments", instrument.ticker, json.dumps(instrument_data))

        pipe.expire("instruments", 420)
        await pipe.execute()

    except Exception as e:
        print(f"Error updating cache: {e}")


async def update_cache_after_delete(ticker: str):
    try:
        redis = await redis_client.get_redis()

        await redis.hdel("instruments", ticker)

        await redis.expire("instruments", 420)

    except Exception as e:
        print(f"Error updating cache after deletion: {e}")


async def load_user_redis(api_key, user):
    data_user_redis = {
        "id": user.uuid,
        "name": user.name,
        "role": user.role,
        "balance": user.balance,
        "is_active": user.is_active,
    }

    redis = await redis_client.get_redis()
    await redis.set(f'user_key:{api_key}', json.dumps(data_user_redis, default=custom_serializer_json), ex=3600)
    return data_user_redis

async def clear_instruments_cache():
    redis = await redis_client.get_redis()
    await redis.delete("instruments")


async def clear_user_cache(api_key):
    redis = await redis_client.get_redis()
    await redis.delete(f'user_key:{api_key}')