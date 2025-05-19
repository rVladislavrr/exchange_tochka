from sqlalchemy import ForeignKey, UUID

from src.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column, relationship


class TradeLog(Base):
    __tablename__ = 'trade_log'
    id: Mapped[int] = mapped_column(primary_key=True)
    buy_order_id: Mapped[UUID] = mapped_column(ForeignKey('orders.uuid'))
    sell_order_id: Mapped[UUID] = mapped_column(ForeignKey('orders.uuid'))
    price: Mapped[float] = mapped_column(nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False)

    buy_order = relationship("Orders", back_populates="buy_trades", foreign_keys=[buy_order_id])
    sell_order = relationship("Orders", back_populates="sell_trades", foreign_keys=[sell_order_id])
    user_trade_history = relationship("UserTradeHistory", back_populates="trade")