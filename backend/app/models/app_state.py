from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AppState(Base):
    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

