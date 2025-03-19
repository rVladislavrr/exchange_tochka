from sqlalchemy import ForeignKey

from src.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column

class UserBalances(Base):
    __tablename__ = 'user_balances'

    id: Mapped[int] = mapped_column(primary_key=True)
    user_uuid: Mapped[str] = mapped_column(ForeignKey('users.uuid'))
    instrument_id: Mapped[str] = mapped_column(ForeignKey('instruments.id'))
    available_balance: Mapped[float] = mapped_column()
    frozen_balance: Mapped[float] = mapped_column()

