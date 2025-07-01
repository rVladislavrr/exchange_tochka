from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload


from src.db.base import BaseManager
from src.models import Orders
from src.models.orders import TypeEnum, SideEnum, StatusEnum
from src.schemas.order import MarketOrder


class OrderManager(BaseManager):
    model = Orders

    async def create_orderOrm(self, user, session, instrument_id, order_data):
        orders = self.model(
            user_uuid=user.id,
            instrument_id=instrument_id,
            order_type=TypeEnum.MARKET_ORDER if isinstance(order_data, MarketOrder) else TypeEnum.LIMIT_ORDER,
            side=SideEnum.BUY if order_data.direction.value == "BUY" else SideEnum.SELL,
            qty=order_data.qty,
            status=StatusEnum.EXECUTED if isinstance(order_data, MarketOrder) else StatusEnum.NEW,
            price=None if isinstance(order_data, MarketOrder) else order_data.price,
            filled=None if isinstance(order_data, MarketOrder) else 0,
        )
        session.add(orders)
        await session.flush()
        await session.refresh(orders)
        return orders

    @staticmethod
    async def get_order(session, order_id, user_id):
        orderOrm = (await session.execute(
            select(Orders).options(selectinload(Orders.instrument)).where(Orders.uuid == order_id,
                                                                          Orders.user_uuid == user_id)
        )).scalars().one_or_none()
        if not orderOrm:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="Order not found")


orderManager = OrderManager()
