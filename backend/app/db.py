"""Database models (SQLAlchemy 2.0). SQLite by default, PostgreSQL via DATABASE_URL."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from . import storage

DATABASE_URL = storage.database_url()

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    thresholds: Mapped[dict] = mapped_column(JSON, default=dict)
    availability: Mapped[dict] = mapped_column(JSON, default=dict)
    ics_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    strava: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    strava_athlete_id: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    # body, nutrition goal, schedule window, gym routines, timezone … (see services.SETTINGS_DEFAULTS)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    plans: Mapped[list["Plan"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    goal: Mapped[dict] = mapped_column(JSON)
    weeks: Mapped[list] = mapped_column(JSON)
    projection: Mapped[dict] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

    user: Mapped[User] = relationship(back_populates="plans")


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    plan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), nullable=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    sport: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(120))
    dsl: Mapped[str] = mapped_column(Text)
    minutes: Mapped[int] = mapped_column(Integer)
    tss: Mapped[float] = mapped_column(Float)
    distance_m: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(10), default="planned")  # planned | done | missed
    actual_minutes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    actual_tss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    actual_distance_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rpe: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="plan")   # plan | custom | strava | rebalance
    external_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    start_time: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)   # "HH:MM", from the calendar
    # before a "life happens" change (no equipment today, only 15 minutes): kept so it can be undone
    original_dsl: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    original_title: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)


class Food(Base):
    """A food with nutrients per 100 g, cached from a public database or created by a user."""
    __tablename__ = "foods"
    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_food_source"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(10), index=True)          # cnf | off | usda | custom
    source_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255), index=True)
    brand: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    barcode: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)
    kcal: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    protein: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    carbs: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fiber: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sugar: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sodium_mg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    servings: Mapped[list] = mapped_column(JSON, default=list)           # [{"label": "1 cup", "grams": 240}]
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    # recipes: [{"food_id", "name", "grams"}]; nutrients above are per 100 g of the finished dish
    ingredients: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    recipe_weight_g: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    recipe_servings: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # deleting a food hides it from search; logged history keeps its own snapshot
    hidden: Mapped[Optional[bool]] = mapped_column(Boolean, default=False, nullable=True)


class CnfIndex(Base):
    """Name index of the Canadian Nutrient File (~5,700 foods), cached for local search."""
    __tablename__ = "cnf_index"
    code: Mapped[int] = mapped_column(primary_key=True)
    name_fr: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    name_en: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class FoodEntry(Base):
    """One logged item. Nutrients are snapshotted so history never changes retroactively."""
    __tablename__ = "food_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    meal: Mapped[str] = mapped_column(String(12))                         # breakfast | lunch | dinner | snack | training
    food_id: Mapped[Optional[int]] = mapped_column(ForeignKey("foods.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    grams: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    kcal: Mapped[float] = mapped_column(Float)
    protein: Mapped[float] = mapped_column(Float, default=0)
    carbs: Mapped[float] = mapped_column(Float, default=0)
    fat: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WeightEntry(Base):
    __tablename__ = "weights"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_weight_day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)
    kg: Mapped[float] = mapped_column(Float)


class DayLog(Base):
    """Per-day state: food log marked complete, evening reflection."""
    __tablename__ = "day_logs"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_daylog"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)
    food_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    reflection: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    checks: Mapped[dict] = mapped_column(JSON, default=dict)               # manual checklist states {key: bool}


class Habit(Base):
    __tablename__ = "habits"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(120))
    weekdays: Mapped[list] = mapped_column(JSON, default=lambda: [0, 1, 2, 3, 4, 5, 6])
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class CalendarSource(Base):
    __tablename__ = "calendar_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(Text)                                 # secret iCal address — never returned in full
    ics: Mapped[Optional[str]] = mapped_column(Text, nullable=True)        # last fetched body
    last_sync: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Deadline(Base):
    """An exam, assignment or quiz the study planner works toward."""
    __tablename__ = "deadlines"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    course: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(12), default="exam")          # exam | assignment | quiz
    due_date: Mapped[date] = mapped_column(Date, index=True)
    due_time: Mapped[str] = mapped_column(String(5), default="23:59")      # local "HH:MM"; exam start time
    end_time: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)   # exams: when it ends
    weight: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # % of the course grade
    hours: Mapped[Optional[float]] = mapped_column(Float, nullable=True)   # revision hours wanted (None = estimate)
    location: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class StudyBlock(Base):
    __tablename__ = "study_blocks"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    deadline_id: Mapped[int] = mapped_column(ForeignKey("deadlines.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    start_time: Mapped[str] = mapped_column(String(5))
    minutes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="planned")     # planned | done | missed
    source: Mapped[str] = mapped_column(String(10), default="auto")        # auto | manual


def init_db() -> None:
    Base.metadata.create_all(engine)
    add_missing_columns()
    storage.daily_backup(DATABASE_URL)


def add_missing_columns() -> list[str]:
    """Tiny forward-only migration: add columns that exist in the models but not yet in the
    database (new features on an existing install). Columns are added as nullable, so old
    rows are untouched; nothing is ever dropped or rewritten."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    added = []
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    ddl = col.type.compile(dialect=engine.dialect)
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ddl}'))
                    added.append(f"{table.name}.{col.name}")
    return added


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
