from datetime import datetime
from typing import final

from sqlalchemy import (
    Connection,
    Constraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
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
from geoalchemy2 import Geography, WKBElement


class Base(DeclarativeBase): ...


class TimestampsMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now(), onupdate=now()
    )


@final
class Server(Base):
    "Server from which we read data files."

    __tablename__ = "servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    hostname: Mapped[str] = mapped_column(nullable=False)

    files: Mapped["IngestedFile"] = relationship(back_populates="server")

    __table_args__: tuple[Constraint] = (
        UniqueConstraint("hostname", name="servers_name_unique"),
    )


@final
class IngestedFile(Base):
    "Tracks data files which have been already inserted into the database."

    __tablename__ = "ingested_files"

    server_id: Mapped[int] = mapped_column(ForeignKey(Server.id), primary_key=True)
    server: Mapped[Server] = relationship()

    path: Mapped[str] = mapped_column(primary_key=True)

    processed_at: Mapped[datetime] = mapped_column(DateTime, default=now())

    __table_args__: tuple[Constraint | Index] = (
        Index("ingested_files_server_id", "server_id"),
    )


@final
class Station(Base, TimestampsMixin):
    """Represents a real-life ensemble of sensors (a sensor station),
    installed together in the same location.
    """

    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(nullable=False)
    location: Mapped[WKBElement] = mapped_column(
        Geography(geometry_type="POINT", srid=4326), nullable=False
    )

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
class MeteorologicalStationInfo(Base, TimestampsMixin):
    "Extra fields specific to meteorological stations."

    __tablename__ = "meteorological_station_infos"

    station_id: Mapped[int] = mapped_column(ForeignKey(Station.id), primary_key=True)
    station: Mapped[Station] = relationship()

    code: Mapped[str] = mapped_column()
    """Unique code identifying this station (and its directory name on the meteorological data FTP server).

    Consists of the county name and location name, joined by a dash. Example: `AG-Dmb`
    """

    __table_args__: tuple[Constraint] = (
        UniqueConstraint("code", name="meteorological_station_infos_code_unique"),
    )


@final
class EddyCovarianceStationInfo(Base, TimestampsMixin):
    "Extra fields specific to eddy covariance stations."

    __tablename__ = "eddy_covariance_station_infos"

    station_id: Mapped[int] = mapped_column(ForeignKey(Station.id), primary_key=True)
    station: Mapped[Station] = relationship()

    sftp_directory_name: Mapped[str] = mapped_column()
    "Name of subdirectory containing this station's data files on the SFTP server."

    code: Mapped[str] = mapped_column()
    "Unique code identifying this station's data files."

    __table_args__: tuple[Constraint, Constraint] = (
        UniqueConstraint(
            "sftp_directory_name",
            name="eddy_covariance_station_infos_sftp_directory_name_unique",
        ),
        UniqueConstraint("code", name="eddy_covariance_station_infos_code_unique"),
    )


class Measurement(Base):
    __abstract__ = True

    station_id: Mapped[int] = mapped_column(ForeignKey(Station.id), primary_key=True)

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )


@final
class Meteorological20SecondMeasurement(Measurement):
    __tablename__ = "meteorological_20s_measurements"

    station: Mapped[Station] = relationship(
        back_populates="meteorological_20_second_measurements"
    )

    irradiance: Mapped[float] = mapped_column(Float(6), nullable=False)
    unit_temperature_sr30: Mapped[float] = mapped_column(Float(6), nullable=False)
    error_code_sr30: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    licor_irradiance: Mapped[float] = mapped_column(Float(6), nullable=True)
    cuv5_irradiance: Mapped[float] = mapped_column(Float(6), nullable=True)


@final
class Meteorological30SecondMeasurement(Measurement):
    __tablename__ = "meteorological_30s_measurements"

    station: Mapped[Station] = relationship(
        back_populates="meteorological_30_second_measurements"
    )

    wind_direction: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    wind_speed: Mapped[float] = mapped_column(Float, nullable=False)
    current_weather_code: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    visibility: Mapped[int] = mapped_column(Integer, nullable=False)
    particles_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    intensity: Mapped[float] = mapped_column(Float(6), nullable=False)
    air_temp_cs125: Mapped[float] = mapped_column(Float(6), nullable=False)
    air_pressure: Mapped[float] = mapped_column(Float(6), nullable=False)
    air_humidity: Mapped[float] = mapped_column(Float(6), nullable=False)
    air_temperature: Mapped[float] = mapped_column(Float(6), nullable=False)
    data_logger_voltage: Mapped[float] = mapped_column(Float(6), nullable=False)


@final
class Meteorological60SecondMeasurement(Measurement):
    __tablename__ = "meteorological_60s_measurements"

    station: Mapped[Station] = relationship(
        back_populates="meteorological_60_second_measurements"
    )

    snow_depth: Mapped[float] = mapped_column(Float(6), nullable=False)
    total_rain: Mapped[float] = mapped_column(Float(6), nullable=False)
    soil_temperature: Mapped[float] = mapped_column(Float(6), nullable=False)
    soil_humidity: Mapped[float] = mapped_column(Float(6), nullable=False)
    measurement_quality_sr50a: Mapped[int] = mapped_column(SmallInteger, nullable=False)


@final
class EddyCovarianceMeasurement(Measurement):
    __tablename__ = "eddy_covariance_measurements"

    station: Mapped[Station] = relationship(
        back_populates="eddy_covariance_measurements"
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


@listens_for(Base.metadata, "before_create")
def _configure_extensions(target: MetaData, connection: Connection, **kw) -> None:
    "Creates required database extensions before creating the tables."

    check_first = kw.get("checkfirst", False)

    if check_first:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    else:
        connection.execute(text("CREATE EXTENSION postgis"))


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
