from sqlalchemy import ForeignKey
from sqlalchemy.orm import mapped_column, Mapped, relationship

from src.models.base import Base
# не актуальная таблица
class UserTradeHistory(Base):
    __tablename__ = 'user_trade_history'

    id: Mapped[int] = mapped_column(primary_key=True)
    user_uuid: Mapped[int] = mapped_column(ForeignKey('users.uuid'), nullable=False)
    trade_id: Mapped[int] = mapped_column(ForeignKey('trade_log.id'), nullable=False)
    action: Mapped[str] = mapped_column(nullable=False, )
    price: Mapped[float] = mapped_column(nullable=False, )
    quantity: Mapped[float] = mapped_column(nullable=False, )

    user = relationship("Users", back_populates="trade_history")
    trade = relationship("TradeLog", back_populates="user_trade_history")