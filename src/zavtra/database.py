"""Хранение MVP: PostgreSQL для сервера, SQLite для локального запуска."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Point(Base):
    __tablename__ = "points"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(100), default="Europe/Moscow")
    revision: Mapped[int] = mapped_column(default=0)


class Member(Base):
    __tablename__ = "members"
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    point_id: Mapped[int] = mapped_column(ForeignKey("points.id"), index=True)


class Invite(Base):
    __tablename__ = "invites"
    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    point_id: Mapped[int] = mapped_column(ForeignKey("points.id"))
    expires_at: Mapped[int] = mapped_column(BigInteger)
    consumed_by: Mapped[int | None] = mapped_column(BigInteger)


class Ingredient(Base):
    __tablename__ = "ingredients"
    __table_args__ = (UniqueConstraint("point_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    point_id: Mapped[int] = mapped_column(ForeignKey("points.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    unit: Mapped[str] = mapped_column(String(5))
    price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    co: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    cu: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    step: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=1)


class Recipe(Base):
    __tablename__ = "recipes"
    point_id: Mapped[int] = mapped_column(ForeignKey("points.id"), primary_key=True)
    dish: Mapped[str] = mapped_column(String(100), primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"), primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6))


class SaleRecord(Base):
    __tablename__ = "sales"
    point_id: Mapped[int] = mapped_column(ForeignKey("points.id"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    dish: Mapped[str] = mapped_column(String(100), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer)


class Stock(Base):
    __tablename__ = "stocks"
    point_id: Mapped[int] = mapped_column(ForeignKey("points.id"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"), primary_key=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[int] = mapped_column(primary_key=True)
    point_id: Mapped[int] = mapped_column(ForeignKey("points.id"), index=True)
    target_date: Mapped[date] = mapped_column(Date)
    input_revision: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(20), default="draft")
    snapshot: Mapped[dict] = mapped_column(JSON)
    edits: Mapped[dict] = mapped_column(JSON, default=dict)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("point_id", "target_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    point_id: Mapped[int] = mapped_column(ForeignKey("points.id"), index=True)
    target_date: Mapped[date] = mapped_column(Date)
    recommendation_id: Mapped[int] = mapped_column(ForeignKey("recommendations.id"), unique=True)
    confirmed_by: Mapped[int] = mapped_column(BigInteger)
    confirmed_at: Mapped[str] = mapped_column(String(40))
    items: Mapped[list] = mapped_column(JSON)


class Database:
    def __init__(self, url: str):
        if url.startswith("sqlite:///"):
            path = url.removeprefix("sqlite:///")
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30}
            if url.startswith("sqlite:")
            else {},
            pool_pre_ping=True,
        )
        if url.startswith("sqlite:"):

            @event.listens_for(self.engine, "connect")
            def configure_sqlite(connection, _record):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=30000")

        self.session = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self) -> None:
        # Bootstrap первой схемы. Последующие изменения потребуют миграций.
        Base.metadata.create_all(self.engine)
