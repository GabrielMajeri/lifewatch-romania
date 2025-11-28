from datetime import datetime
from typing import Any, final

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
    """Represents a real-life ensemble of sensors (a sensor station),
    installed together in the same location.
    """

    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(nullable=False)
    extra: Mapped[dict[str, Any]] = mapped_column(nullable=False, default={})

    sensors: Mapped[list["Sensor"]] = relationship(back_populates="station")

    __table_args__: tuple[Constraint] = (
        UniqueConstraint("name", name="stations_name_unique"),
    )


@final
class SensorType(Base, TimestampsMixin):
    """Uniquely identifies a type (model) of sensor."""

    __tablename__ = "sensor_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True, nullable=False)
    description: Mapped[str] = mapped_column(nullable=False, default="")

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
    name: Mapped[str] = mapped_column(String, nullable=False, default="")
    serial_number: Mapped[str] = mapped_column(nullable=False)

    station: Mapped[Station] = relationship(back_populates="sensors")
    measurements: Mapped["Measurement"] = relationship(back_populates="sensor")

    __table_args__: tuple[Constraint] = (
        UniqueConstraint(
            "sensor_type_id",
            "serial_number",
            name="sensors_serial_number_unique_per_type",
        ),
    )


@final
class Variable(Base, TimestampsMixin):
    __tablename__ = "variables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Human-readable name (e.g. `CO2 concentration`)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # Longer description of this variable
    description: Mapped[str] = mapped_column(String, default="")
    units: Mapped[str] = mapped_column(String, nullable=False)

    extra: Mapped[dict[str, Any]] = mapped_column(nullable=False, default={})

    measurements: Mapped["Measurement"] = relationship(back_populates="variable")

    __table_args__: tuple[Constraint, ...] = (
        UniqueConstraint("name", name="variables_name_unique"),
    )

    def icos_label(self) -> str | None:
        """Machine-readable label used to uniquely identify this variable,
        as defined by ICOS (e.g. `CO2_CONC`).
        """
        return self.extra.get("icos_label", None)


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
    if kw.get("checkfirst", False) == True:
        stmt = text(
            f"SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = '{Measurement.__tablename__}'"
        )
        result = connection.execute(stmt)
        if len(result.all()) > 0:
            # print(f"Table '{Measurement.__tablename__}' is already a hypertable")
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
