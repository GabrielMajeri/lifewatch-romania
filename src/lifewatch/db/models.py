from datetime import datetime
from sqlalchemy import (
    Connection,
    DateTime,
    ForeignKey,
    Integer,
    Interval,
    MetaData,
    String,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.event import listens_for
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.functions import now


class Base(DeclarativeBase):
    pass


class TimestampsMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now(), onupdate=now()
    )


class Sensor(Base, TimestampsMixin):
    __tablename__ = "sensors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String)

    __table_args__ = (UniqueConstraint("name", name="sensors_name_unique"),)


class Variable(Base, TimestampsMixin):
    __tablename__ = "variables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Machine-readable label used to uniquely identify this variable,
    # similar to what ICOS does (e.g. `CO2_CONC`)
    label: Mapped[str] = mapped_column(String)
    # Human-readable name (e.g. `CO2 concentration`)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String, default="")
    unit: Mapped[str] = mapped_column(String)

    __table_args__ = (
        UniqueConstraint("label", name="variables_label_unique"),
        UniqueConstraint("name", name="variables_name_unique"),
    )


class Measurement(Base):
    __tablename__ = "measurements"

    sensor_id: Mapped[int] = mapped_column(ForeignKey(Sensor.id), primary_key=True)
    variable_id: Mapped[int] = mapped_column(ForeignKey(Variable.id), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    value: Mapped[float]


@listens_for(Base.metadata, "after_create")
def configure_hypertables(target: MetaData, connection: Connection, **kw):
    # Convert Measurements to a TigerData/TimeScaleDB hypertable
    # connection.execute(select(func.now()))

    stmt = select(
        func.create_hypertable(
            Measurement.__tablename__,
            func.by_range("timestamp", func.cast("1 day", Interval)),
        )
    )
    connection.execute(stmt)
