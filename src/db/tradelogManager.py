from src.db.base import BaseManager
from src.models import TradeLog


class TradeLogManager(BaseManager):
    model = TradeLog

    async def create_trade_log(self, data) -> TradeLog:
        pass
