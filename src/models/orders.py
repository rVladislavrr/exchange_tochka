import enum
from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey, Enum, UUID, func

from src.models.base import Base


class StatusEnum(enum.Enum):
    NEW = "NEW"
    EXECUTED = "EXECUTED"
    PARTIALLY_EXECUTED = "PARTIALLY_EXECUTED"
    CANCELLED = "CANCELLED"
    PENDING = "PENDING"


class TypeEnum(enum.Enum):
    LIMIT_ORDER = 'LIMIT_ORDER'
    MARKET_ORDER = 'MARKET_ORDER'


class SideEnum(enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class Orders(Base):
    __tablename__ = 'orders'

    uuid: Mapped[UUID] = mapped_column(UUID(as_uuid=True), server_default=func.gen_random_uuid(),
                                       nullable=False, index=True, primary_key=True)
    user_uuid: Mapped[str] = mapped_column(ForeignKey('users.uuid'))
    instrument_id: Mapped[int] = mapped_column(ForeignKey('instruments.id'))
    order_type: Mapped[TypeEnum] = mapped_column(Enum(TypeEnum), nullable=False, index=True)
    side: Mapped[SideEnum] = mapped_column(Enum(SideEnum), nullable=False)
    price: Mapped[float] = mapped_column(nullable=True, index=True)
    qty: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[StatusEnum] = mapped_column(Enum(StatusEnum), nullable=False, default=StatusEnum.NEW, index=True)
    filled: Mapped[int] = mapped_column(nullable=False)
    activation_time: Mapped[datetime] = mapped_column(nullable=True)
