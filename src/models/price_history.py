from sqlalchemy import ForeignKey

from src.models.base import Base
from sqlalchemy.orm import Mapped, mapped_column

class PriceHistory(Base):
    __tablename__ = 'price_history'

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey('instruments.id'), nullable=False)
    price: Mapped[float] = mapped_column(nullable=False)

