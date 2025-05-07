from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String


from src.models.base import Base


class Instruments(Base):
    __tablename__ = 'instruments'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(nullable=False)
    current_price: Mapped[float] = mapped_column(default=0.0, nullable=False)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    



    
