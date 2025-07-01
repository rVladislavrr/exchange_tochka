import sys
from pathlib import Path
import asyncio

project_root = Path(__file__).parent.parent.parent
sys.path.append(str(project_root))

from src.tasks.orders import match_order_limit
from src.redis_conn import redis_client


async def main():
    r = await redis_client.get_redis()
    while True:
        if value := await r.rpop("limit_orders"):
            uuid_order, ticker, request_id = value.split(':')
            try:
                await match_order_limit(uuid_order, ticker, request_id, r)
            except Exception as e:
                print(e)
            finally:
                print('finished')


if __name__ == '__main__':
    asyncio.run(main())
