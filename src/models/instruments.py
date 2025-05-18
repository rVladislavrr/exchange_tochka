from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Index, UniqueConstraint

from src.models.base import Base


class Instruments(Base):
    __tablename__ = 'instruments'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(nullable=False)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)

    __table_args__ = (
        Index(
            'uq_active_ticker',
            'ticker',
            unique=True,
            postgresql_where=(is_active == True)
        ),
    )
    



    
