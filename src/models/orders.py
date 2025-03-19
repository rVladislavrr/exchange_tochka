import enum
from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey, String, Enum

from src.models.base import Base

class StatusEnum(enum.Enum):
    OPEN = "open"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    PENDING = "pending"

class Orders(Base):
    __tablename__ = 'orders'

    id: Mapped[int] = mapped_column(primary_key=True)
    user_uuid: Mapped[str] = mapped_column(ForeignKey('users.uuid'))
    instrument: Mapped[int] = mapped_column(ForeignKey('instruments.id'))
    order_type: Mapped[str] = mapped_column(String(6), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    price: Mapped[float] = mapped_column( nullable=False)
    quantity: Mapped[float] = mapped_column( nullable=False)
    status: Mapped[StatusEnum] = mapped_column(Enum(StatusEnum), nullable=False, default=StatusEnum.OPEN)
    activation_time: Mapped[datetime] = mapped_column(nullable=True)
