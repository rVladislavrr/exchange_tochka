from sqlalchemy import ForeignKey

from src.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column

class TradeLog(Base):
    __tablename__ = 'trade_log'
    id: Mapped[int] = mapped_column(primary_key=True)
    buy_order_id: Mapped[int] = mapped_column(ForeignKey('orders.uuid'))
    sell_order_id: Mapped[int] = mapped_column(ForeignKey('orders.uuid'))
    price: Mapped[float] = mapped_column(nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False)