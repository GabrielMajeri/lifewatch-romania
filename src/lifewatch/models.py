from datetime import datetime
from typing import final

from sqlalchemy import (
    Connection,
    Constraint,
    DateTime,
    ForeignKey,
    Integer,
    Interval,
    MetaData,
    String,
    UniqueConstraint,
    select,
    text,
)
from sqlalchemy.event import listens_for
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.sql.functions import now

from .db import Base


class TimestampsMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now(), onupdate=now()
    )


@final
class Station(Base, TimestampsMixin):
    """Represents a real-life ensemble of sensors (a sensor station/pack),
    usually soldered to the same support structure.
    """

    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(nullable=False)

    sensors: Mapped[list["Sensor"]] = relationship(back_populates="station")

    __table_args__: tuple[Constraint] = (
        UniqueConstraint("name", name="stations_name_unique"),
    )


class SensorType(Base, TimestampsMixin):
    """Uniquely identifies a type of sensor."""

    __tablename__ = "sensor_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    __table_args__: tuple[Constraint] = (
        UniqueConstraint("name", name="sensor_types_name_unique"),
    )


@final
class Sensor(Base, TimestampsMixin):
    __tablename__ = "sensors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey(Station.id), nullable=False)
    sensor_type_id: Mapped[int] = mapped_column(
        ForeignKey(SensorType.id), nullable=False
    )
    name: Mapped[str] = mapped_column(String)
    serial_number: Mapped[str] = mapped_column(nullable=False)

    __table_args__: tuple[Constraint] = (
        UniqueConstraint("name", name="sensors_name_unique"),
    )


@final
class Variable(Base, TimestampsMixin):
    __tablename__ = "variables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Machine-readable label used to uniquely identify this variable,
    # similar to what ICOS does (e.g. `CO2_CONC`)
    label: Mapped[str] = mapped_column(String, nullable=False)
    # Human-readable name (e.g. `CO2 concentration`)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # Longer description of this variable
    description: Mapped[str] = mapped_column(String, default="")
    unit: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__: tuple[Constraint, ...] = (
        UniqueConstraint("label", name="variables_label_unique"),
        UniqueConstraint("name", name="variables_name_unique"),
    )


@final
class Measurement(Base):
    __tablename__ = "measurements"

    sensor_id: Mapped[int] = mapped_column(ForeignKey(Sensor.id), primary_key=True)
    variable_id: Mapped[int] = mapped_column(ForeignKey(Variable.id), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    value: Mapped[float] = mapped_column(nullable=False)

    sensor: Mapped[Sensor] = relationship(back_populates="measurements")
    variable: Mapped[Variable] = relationship(back_populates="measurements")


@listens_for(Base.metadata, "after_create")
def configure_hypertables(target: MetaData, connection: Connection, **kw):
    stmt = text(
        f"SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = '{Measurement.__tablename__}'"
    )
    result = connection.execute(stmt)
    if len(result.all()) > 0:
        print(f"Table '{Measurement.__tablename__}' is already a hypertable")
        return

    # Convert `Measurements` to a TigerData hypertable
    stmt = select(
        func.create_hypertable(
            Measurement.__tablename__,
            func.by_range("timestamp", func.cast("1 day", Interval)),
        )
    )
    connection.execute(stmt)

    # Enable columnstore
    stmt = text(f"""ALTER TABLE {Measurement.__tablename__} SET (
        tsdb.enable_columnstore = true,
        tsdb.segmentby = 'sensor_id, variable_id'
    );""")
    connection.execute(stmt)

    # Add a columnstore policy
    stmt = text(
        f"CALL add_columnstore_policy('{Measurement.__tablename__}', after => INTERVAL '1 day')"
    )
    connection.execute(stmt)
