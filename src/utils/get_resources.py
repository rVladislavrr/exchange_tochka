import json

from src.db.instrumentManager import instrumentsManager
from src.redis_conn import redis_client
from src.utils.redis_utils import update_instruments_cache


async def get_instruments(session, background_tasks):
    try:
        redis = await redis_client.get_redis()

        if await redis.exists("instruments"):
            print("cache")
            all_instruments = await redis.hgetall("instruments")
            try:
                return [json.loads(value) for value in all_instruments.values()]
            except json.JSONDecodeError as e:
                print(f"Ошибка декодирования JSON из Redis: {e}")

        instruments = await instrumentsManager.get_all(session)

        if not instruments:
            return []
        background_tasks.add_task(update_instruments_cache, instruments)
        return instruments
    except Exception as e:
        print(f"Ошибка при получении инструментов: {e}")
        return []