import datetime
import enum

from geoalchemy2 import Geometry
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, Table, Enum, Text
)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


class SensorStatusEnum(enum.Enum):
    active = "active"
    inactive = "inactive"
    calibration_needed = "calibration_needed"


class QualityFlagEnum(enum.Enum):
    good = 0
    suspect = 1
    invalid = 2
    estimated = 3


class SensorType(Base):
    __tablename__ = 'sensor_types'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)


class MeasurableParameter(Base):
    __tablename__ = 'parameters'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    unit = Column(String, nullable=False)


sensor_parameter = Table(
    'sensor_parameter',
    Base.metadata,
    Column('sensor_type_id', ForeignKey('sensor_types.id'), primary_key=True),
    Column('parameter_id', ForeignKey('parameters.id'), primary_key=True)
)


class Station(Base):
    __tablename__ = 'stations'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    sensors = relationship("Sensor", back_populates="station")
    temporal_resolutions = relationship("TemporalResolution", secondary="station_temporal_resolution")


class Sensor(Base):
    __tablename__ = 'sensors'
    id = Column(Integer, primary_key=True)
    station_id = Column(ForeignKey('stations.id'), nullable=False)
    sensor_type_id = Column(ForeignKey('sensor_types.id'), nullable=False)
    model = Column(String)
    manufacturer = Column(String)
    serial_number = Column(String)
    install_date = Column(DateTime)
    decommission_date = Column(DateTime)
    status = Column(Enum(SensorStatusEnum), nullable=False)
    note = Column(Text)

    station = relationship("Station", back_populates="sensors")
    type = relationship("SensorType")
    measurements = relationship("Measurement", back_populates="sensor")


class StationPosition(Base):
    __tablename__ = 'station_positions'
    id = Column(Integer, primary_key=True)
    station_id = Column(ForeignKey('stations.id'), nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    location = Column(Geometry(geometry_type='POINT', srid=4326), nullable=False)
    county = Column(String)
    locality = Column(String)


class TemporalResolution(Base):
    __tablename__ = 'temporal_resolutions'
    id = Column(Integer, primary_key=True)
    label = Column(String, nullable=False)  # e.g., "30 minutes", "1 hour"


sensor_temporal_resolution = Table(
    'sensor_temporal_resolution',
    Base.metadata,
    Column('station_id', ForeignKey('stations.id'), primary_key=True),
    Column('temporal_resolution_id', ForeignKey('temporal_resolutions.id'), primary_key=True)
)

#aici nu ar trebui station, ci sensor temporal resolution.

class Measurement(Base):
    __tablename__ = 'measurements'
    id = Column(Integer, primary_key=True)
    sensor_id = Column(ForeignKey('sensors.id'), nullable=False)
    parameter_id = Column(ForeignKey('parameters.id'), nullable=False)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    value = Column(Float)
    quality_flag = Column(Enum(QualityFlagEnum))

    sensor = relationship("Sensor", back_populates="measurements")
    parameter = relationship("MeasurableParameter")


class SensorCalibration(Base):
    __tablename__ = 'sensor_calibrations'
    id = Column(Integer, primary_key=True)
    sensor_id = Column(ForeignKey('sensors.id'), nullable=False)
    calibration_date = Column(DateTime)
    calibrated_by = Column(String)
    calibration_details = Column(Text)

    sensor = relationship("Sensor")