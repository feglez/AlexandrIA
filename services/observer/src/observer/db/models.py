"""Database tables (SQLAlchemy declarative models)."""

from datetime import datetime

from sqlalchemy import BigInteger, Integer, MetaData, String, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import DateTime

# Stable constraint names, so Alembic can generate migrations that drop them.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UsageEvent(Base):
    """Ledger: one row per event that counts, written once (append-only).

    The primary key is the event id, so an event delivered twice is only
    counted once.
    """

    __tablename__ = "usage_events"

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(40))
    member: Mapped[str] = mapped_column(Text)
    # Change of the member's library contribution: + published, - removed.
    bytes: Mapped[int] = mapped_column(BigInteger)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class UsageTotal(Base):
    """Totals per member and month (period "YYYY-MM", UTC), kept with the ledger."""

    __tablename__ = "usage_totals"

    member: Mapped[str] = mapped_column(Text, primary_key=True)
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    # Net change of the library contribution during the month.
    contributed_bytes: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    submissions: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    searches: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    questions: Mapped[int] = mapped_column(Integer, server_default=text("0"))
