from datetime import datetime
from typing import final

from sqlalchemy import (
    Connection,
    Constraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Interval,
    MetaData,
    SmallInteger,
    UniqueConstraint,
    select,
    text,
)
from sqlalchemy.event import listens_for
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.sql.functions import now


class Base(DeclarativeBase): ...


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

    eddy_covariance_measurements: Mapped["EddyCovarianceMeasurement"] = relationship(
        back_populates="station"
    )
    meteorological_20_second_measurements: Mapped[
        "Meteorological20SecondMeasurement"
    ] = relationship(back_populates="station")
    meteorological_30_second_measurements: Mapped[
        "Meteorological30SecondMeasurement"
    ] = relationship(back_populates="station")
    meteorological_60_second_measurements: Mapped[
        "Meteorological60SecondMeasurement"
    ] = relationship(back_populates="station")

    __table_args__: tuple[Constraint] = (
        UniqueConstraint("name", name="stations_name_unique"),
    )


@final
class Meteorological20SecondMeasurement(Base):
    __tablename__ = "meteorological_20s_measurements"

    station_id: Mapped[int] = mapped_column(ForeignKey(Station.id), primary_key=True)
    station: Mapped[Station] = relationship(
        back_populates="meteorological_20_second_measurements"
    )

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    irradiance: Mapped[float] = mapped_column(Float(6), nullable=False)
    unit_temperature_sr30: Mapped[float] = mapped_column(Float(6), nullable=False)
    error_code_sr30: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    licor_irradiance: Mapped[float] = mapped_column(Float(6), nullable=False)
    cuv5_irradiance: Mapped[float] = mapped_column(Float(6), nullable=False)


@final
class Meteorological30SecondMeasurement(Base):
    __tablename__ = "meteorological_30s_measurements"

    station_id: Mapped[int] = mapped_column(ForeignKey(Station.id), primary_key=True)
    station: Mapped[Station] = relationship(
        back_populates="meteorological_30_second_measurements"
    )

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    wind_direction: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    wind_speed: Mapped[float] = mapped_column(Float, nullable=False)
    current_weather_code: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    visibility: Mapped[int] = mapped_column(Integer, nullable=False)
    particles_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    intensity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    air_temp_cs125: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    air_pressure: Mapped[float] = mapped_column(Float(6), nullable=False)
    air_humidity: Mapped[float] = mapped_column(Float(6), nullable=False)
    air_temperature: Mapped[float] = mapped_column(Float(6), nullable=False)
    data_logger_voltage: Mapped[float] = mapped_column(Float(6), nullable=False)


@final
class Meteorological60SecondMeasurement(Base):
    __tablename__ = "meteorological_60s_measurements"

    station_id: Mapped[int] = mapped_column(ForeignKey(Station.id), primary_key=True)
    station: Mapped[Station] = relationship(
        back_populates="meteorological_60_second_measurements"
    )

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    snow_depth: Mapped[float] = mapped_column(Float(6), nullable=False)
    total_rain: Mapped[float] = mapped_column(Float(6), nullable=False)
    soil_temperature: Mapped[float] = mapped_column(Float(6), nullable=False)
    soil_humidity: Mapped[float] = mapped_column(Float(6), nullable=False)
    measurement_quality_sr50a: Mapped[int] = mapped_column(SmallInteger, nullable=False)


@final
class EddyCovarianceMeasurement(Base):
    __tablename__ = "eddy_covariance_measurements"

    station_id: Mapped[int] = mapped_column(ForeignKey(Station.id), primary_key=True)
    station: Mapped[Station] = relationship(
        back_populates="eddy_covariance_measurements"
    )

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    nanoseconds: Mapped[int] = mapped_column(nullable=False, default=0)

    co2_concentration: Mapped[float] = mapped_column(Float(6), nullable=False)
    h2o_concentration: Mapped[float] = mapped_column(Float(6), nullable=False)
    co2_raw_power_in_sample_wavelength: Mapped[float] = mapped_column(
        Float(6), nullable=False
    )
    h2o_raw_power_in_sample_wavelength: Mapped[float] = mapped_column(
        Float(6), nullable=False
    )
    co2_raw_power_in_reference_wavelength: Mapped[float] = mapped_column(
        Float(6), nullable=False
    )
    h2o_raw_power_in_reference_wavelength: Mapped[float] = mapped_column(
        Float(6), nullable=False
    )
    co2_molar_fraction: Mapped[float] = mapped_column(Float(6), nullable=False)
    h2o_molar_fraction: Mapped[float] = mapped_column(Float(6), nullable=False)
    co2_dry_mole_fraction: Mapped[float] = mapped_column(Float(6), nullable=False)
    h2o_dry_mole_fraction: Mapped[float] = mapped_column(Float(6), nullable=False)
    co2_mass_density: Mapped[float] = mapped_column(Float(6), nullable=False)
    h2o_mass_density: Mapped[float] = mapped_column(Float(6), nullable=False)
    co2_absorptance: Mapped[float] = mapped_column(Float(6), nullable=False)
    h2o_absorptance: Mapped[float] = mapped_column(Float(6), nullable=False)


def _configure_hypertable(
    connection: Connection,
    table_name: str,
    range_column_name: str = "timestamp",
    segment_by_column_name: str = "station_id",
    check_first: bool = False,
) -> None:
    "Configures the given table as a TimescaleDB hypertable."

    if check_first:
        stmt = text(
            f"SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_name = '{table_name}'"
        )
        result = connection.execute(stmt)
        if len(result.all()) > 0:
            # Table is already a hypertable, nothing to do
            return

    # Convert table to a TigerData hypertable
    stmt = select(
        func.create_hypertable(
            table_name,
            func.by_range(range_column_name, func.cast("1 day", Interval)),  # pyright: ignore[reportArgumentType]
        )
    )
    connection.execute(stmt)

    # Enable columnstore
    stmt = text(f"""ALTER TABLE {table_name} SET (
        tsdb.enable_columnstore = true,
        tsdb.segmentby = '{segment_by_column_name}'
    );""")
    connection.execute(stmt)

    # Add a columnstore policy
    stmt = text(
        f"CALL add_columnstore_policy('{table_name}', after => INTERVAL '1 day')"
    )
    connection.execute(stmt)


@listens_for(Base.metadata, "after_create")
def _configure_hypertables(target: MetaData, connection: Connection, **kw) -> None:  # pyright: ignore[reportUnusedFunction]
    # Determine if the developer wants us to check whether the tables are already hypertables,
    # before trying to apply the conversion operation.
    check_first = kw.get("checkfirst", False)

    measurement_tables = (
        Meteorological20SecondMeasurement.__tablename__,
        Meteorological30SecondMeasurement.__tablename__,
        Meteorological60SecondMeasurement.__tablename__,
        EddyCovarianceMeasurement.__tablename__,
    )

    for table_name in measurement_tables:
        _configure_hypertable(
            connection,
            table_name,
            check_first=check_first,
        )
