import asyncio
import redis.asyncio as redis
from src.config import settings

class RedisClient:

    def __init__(self):
        self.redis = None

    async def connect(self):
        if self.redis is None:
            for attempt in range(3):
                try:
                    self.redis = await redis.from_url(settings.REDIS_BASE_URL, decode_responses=True, encoding='utf-8')
                    await self.redis.ping()
                    print("✅ Successfully connected to Redis")
                    return
                except Exception as e:
                    print(f"⚠️ Redis connection failed (attempt {attempt + 1}/3): {e}")
                    await asyncio.sleep(2)

            print("❌ Could not connect to Redis after 3 attempts")
            raise RuntimeError("Redis connection failed")

    async def get_redis(self):
        if self.redis is None:
            print("🔄 Reconnecting to Redis...")
            await self.connect()

        if self.redis is None:
            raise ConnectionError("❌ Redis is unavailable")
        return self.redis

    async def close(self):
        if self.redis:
            await self.redis.close()


redis_client = RedisClient()