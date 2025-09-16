import datetime
import enum

from geoalchemy2 import Geometry
from sqlalchemy import (
    String, ForeignKey, Table, Enum, Text, Column
)
from sqlalchemy.orm import (
    relationship, declarative_base, Mapped, mapped_column
)

Base = declarative_base()


# --- ENUMS ---

class SensorStatusEnum(enum.Enum):
    active = "active"
    inactive = "inactive"
    calibration_needed = "calibration_needed"


class QualityFlagEnum(enum.Enum):
    good = 0
    suspect = 1
    invalid = 2
    estimated = 3


# --- TABLES ---

class SensorType(Base):
    __tablename__ = 'sensor_types'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)


class MeasuredVariable(Base):
    __tablename__ = 'variables'  # ICOS-style naming

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(nullable=False)
    unit: Mapped[str] = mapped_column(nullable=False)


# ✅ Updated join table name and columns
sensor_variable = Table(
    'sensor_variable',
    Base.metadata,
    Column('sensor_type_id', ForeignKey('sensor_types.id'), primary_key=True),
    Column('variable_id', ForeignKey('variables.id'), primary_key=True)
)


class Station(Base):
    __tablename__ = 'stations'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
    )

    sensors: Mapped[list["Sensor"]] = relationship(back_populates="station")
    temporal_resolutions: Mapped[list["TemporalResolution"]] = relationship(
        secondary="station_temporal_resolution"
    )


class Sensor(Base):
    __tablename__ = 'sensors'

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey('stations.id'), nullable=False)
    sensor_type_id: Mapped[int] = mapped_column(ForeignKey('sensor_types.id'), nullable=False)
    model: Mapped[str] = mapped_column(nullable=True)
    manufacturer: Mapped[str] = mapped_column(nullable=True)
    serial_number: Mapped[str] = mapped_column(nullable=True)
    install_date: Mapped[datetime.datetime] = mapped_column(nullable=True)
    decommission_date: Mapped[datetime.datetime] = mapped_column(nullable=True)
    status: Mapped[SensorStatusEnum] = mapped_column(Enum(SensorStatusEnum), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=True)

    station: Mapped["Station"] = relationship(back_populates="sensors")
    type: Mapped["SensorType"] = relationship()
    measurements: Mapped[list["Measurement"]] = relationship(back_populates="sensor")


class StationPosition(Base):
    __tablename__ = 'station_positions'

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey('stations.id'), nullable=False)
    timestamp: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow())
    location: Mapped[str] = mapped_column(Geometry(geometry_type='POINT', srid=4326), nullable=False)
    county: Mapped[str] = mapped_column(nullable=True)
    locality: Mapped[str] = mapped_column(nullable=True)


class TemporalResolution(Base):
    __tablename__ = 'temporal_resolutions'

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(nullable=False)  # e.g., "30 minutes", "1 hour"


station_temporal_resolution = Table(
    'station_temporal_resolution',
    Base.metadata,
    Column('station_id', ForeignKey('stations.id'), primary_key=True),
    Column('temporal_resolution_id', ForeignKey('temporal_resolutions.id'), primary_key=True)
)


class Measurement(Base):
    __tablename__ = 'measurements'

    id: Mapped[int] = mapped_column(primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey('sensors.id'), nullable=False)
    variable_id: Mapped[int] = mapped_column(ForeignKey('variables.id'), nullable=False)
    timestamp: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow())
    value: Mapped[float] = mapped_column(nullable=True)
    quality_flag: Mapped[QualityFlagEnum] = mapped_column(Enum(QualityFlagEnum), nullable=True)

    sensor: Mapped["Sensor"] = relationship(back_populates="measurements")
    variable: Mapped["MeasuredVariable"] = relationship()


class SensorCalibration(Base):
    __tablename__ = 'sensor_calibrations'

    id: Mapped[int] = mapped_column(primary_key=True)
    sensor_id: Mapped[int] = mapped_column(ForeignKey('sensors.id'), nullable=False)
    calibration_date: Mapped[datetime.datetime] = mapped_column(nullable=True)
    calibrated_by: Mapped[str] = mapped_column(nullable=True)
    calibration_details: Mapped[str] = mapped_column(Text, nullable=True)

    sensor: Mapped["Sensor"] = relationship()
