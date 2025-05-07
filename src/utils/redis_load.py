import json

from src.redis_conn import redis_client
from src.utils.custom_serializer import custom_serializer_json


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
