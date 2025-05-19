from sqlalchemy import func, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
import enum
from src.models.base import Base


class RoleEnum(enum.Enum):
    ADMIN = "ADMIN"
    USER = "USER"


class Users(Base):
    __tablename__ = 'users'

    uuid: Mapped[UUID] = mapped_column(UUID(as_uuid=True), server_default=func.gen_random_uuid(),
                                       nullable=False, index=True, primary_key=True)

    name: Mapped[str] = mapped_column(nullable=True, index=False)
    role: Mapped[RoleEnum] = mapped_column(Enum(RoleEnum), nullable=False, index=False, default=RoleEnum.USER)
    api_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # теперь только в UserBalances
        # balance: Mapped[float] = mapped_column(nullable=False, index=True, default=0)
        # frozen_balance: Mapped[float] = mapped_column(nullable=False, index=True, default=0)

    is_active: Mapped[bool] = mapped_column(default=True)
    # email: Mapped[str] = mapped_column(unique=True, index=True)
    # hash_password: Mapped[str]
    # is_verified: Mapped[bool] = mapped_column(default=False)

    orders = relationship("Orders", back_populates="user")
    balances = relationship("UserBalances", back_populates="user")
    trade_history = relationship("UserTradeHistory", back_populates="user")