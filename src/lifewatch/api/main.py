from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from csv import DictReader
from datetime import datetime, timedelta
from enum import StrEnum
import logging
from os import getenv
from pathlib import Path
import re
from time import perf_counter
from typing import Annotated, Literal, Sequence, TypedDict

import coloredlogs
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
from geoalchemy2.shape import to_shape
from pydantic import BaseModel
from shapely import Point
from sqlalchemy import asc, create_engine, distinct, func, select
from sqlalchemy.orm import Session
import uvicorn

from lifewatch.models import (
    EddyCovarianceMeasurement,
    EddyCovarianceStationInfo,
    Measurement,
    Meteorological20SecondMeasurement,
    Meteorological30SecondMeasurement,
    Meteorological60SecondMeasurement,
    MeteorologicalStationInfo,
    Station,
)

# Load environment variables from the `.env` file (if it exists).
load_dotenv()

DATABASE_URL: str = getenv(
    "DATABASE_URL", "postgresql+psycopg://dev:pwd@localhost:5432/lifewatch"
)

coloredlogs.install(level=logging.INFO)
logger = logging.getLogger(__name__)

engine = create_engine(DATABASE_URL)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator:
    "Lifespan manager/handler for FastAPI app."

    # Try to connect to the database to make sure that it's working
    try:
        with engine.connect() as conn:
            conn.execute(select(1)).scalar_one()
    except:
        logger.error("Failed to connect to database")
        raise

    logger.info("Successfully connected to database")

    yield


app = FastAPI(
    lifespan=lifespan,
    title="LifeWatch Romania API",
    summary="REST API for accessing the data collected by the Romanian branches of the LifeWatch and ICOS projects.",
    description="This API provides access to the public environmental monitoring data collected by the LifeWatch Romania project (https://lifewatch.ro/), part of the LifeWatch ERIC (https://www.lifewatch.eu/).",
)


def get_session() -> Generator[Session, None, None]:
    "Constructs and yields a new SQLAlchemy `Session`."
    with Session(engine) as session:
        yield session


type SessionDep = Annotated[Session, Depends(get_session)]


class HealthStatusResponse(TypedDict):
    status: Literal["healthy"]


@app.get("/health", summary="Check API server health")
def health() -> HealthStatusResponse:
    return {"status": "healthy"}


class VariableKind(StrEnum):
    METEO = "meteorological"
    EDDY_COVARIANCE = "eddy-covariance"


class VariableInfo(TypedDict):
    id: str
    name: str
    kind: VariableKind
    units: str
    description: str


@app.get("/variables", summary="Get list of recorded environmental variables")
def get_variables() -> Sequence[VariableInfo]:
    variable_infos: Sequence[VariableInfo] = []

    variable_infos += [
        VariableInfo(
            id=var.column_name,
            name=var.name,
            kind=VariableKind.METEO,
            units=var.units,
            description=var.description,
        )
        for var in meteo_variables
    ]

    variable_infos += [
        VariableInfo(
            id=var.column_name,
            name=var.name,
            kind=VariableKind.EDDY_COVARIANCE,
            units=var.units,
            description=var.description,
        )
        for var in eddy_covariance_variables
    ]

    return variable_infos


class StationKind(StrEnum):
    METEO = "meteorological"
    EDDY_COVARIANCE = "eddy-covariance"


class StationEntry(TypedDict):
    id: int
    name: str
    kind: StationKind | None


@app.get("/stations", summary="Retrieve the list of stations")
def get_stations(session: SessionDep) -> Sequence[StationEntry]:
    logger.info("GET /stations")

    stmt = (
        select(
            Station.id,
            Station.name,
            MeteorologicalStationInfo.code,
            EddyCovarianceStationInfo.code,
        )
        .join(MeteorologicalStationInfo, isouter=True)
        .join(EddyCovarianceStationInfo, isouter=True)
    )
    results = session.execute(stmt)

    stations: list[StationEntry] = []
    for row in results:
        kind = None
        if row[2] is not None:
            kind = StationKind.METEO
        elif row[3] is not None:
            kind = StationKind.EDDY_COVARIANCE

        stations.append({"id": row[0], "name": row[1], "kind": kind})

    return stations


class VariableMetadata(BaseModel):
    table_name: str
    column_name: str
    name: str
    units: str
    description: str


seed_data_directory = Path(__file__).parents[3] / "seed_data"
with open(seed_data_directory / "variables.csv") as file:
    reader = DictReader(file)
    variable_metadata = [VariableMetadata.model_validate(row) for row in reader]

meteo_variable_table_name_pattern = re.compile(r"^meteorological_\d+s_measurements$")
meteo_variables = [
    md
    for md in variable_metadata
    if meteo_variable_table_name_pattern.match(md.table_name)
]
eddy_covariance_variables = [
    md for md in variable_metadata if md.table_name == "eddy_covariance_measurements"
]

variable_ids = [meta.column_name for meta in variable_metadata]
assert len(variable_ids) == len(set(variable_ids)), (
    "Variable column names have to be unique, for now"
)

measurements_tables: dict[str, type[Measurement]] = {
    "meteorological_20s_measurements": Meteorological20SecondMeasurement,
    "meteorological_30s_measurements": Meteorological30SecondMeasurement,
    "meteorological_60s_measurements": Meteorological60SecondMeasurement,
    "eddy_covariance_measurements": EddyCovarianceMeasurement,
}


class VariableEntry(TypedDict):
    name: str
    label: str
    units: str
    description: str


def variable_entry_from_metadata(metadata: VariableMetadata) -> VariableEntry:
    return {
        "name": metadata.column_name,
        "label": metadata.name,
        "units": metadata.units,
        "description": metadata.description,
    }


meteo_variables_entries = [variable_entry_from_metadata(md) for md in meteo_variables]
eddy_covariance_variables_entries = [
    variable_entry_from_metadata(md) for md in eddy_covariance_variables
]


class GeographicPosition(TypedDict):
    latitude: float
    longitude: float


class StationInformation(StationEntry):
    location: GeographicPosition
    variables: list[VariableEntry]


def get_station_by_id(session: Session, station_id: int) -> StationEntry:
    stmt = (
        select(
            Station.name,
            MeteorologicalStationInfo.code,
            EddyCovarianceStationInfo.code,
        )
        .where(Station.id == station_id)
        .join(MeteorologicalStationInfo, isouter=True)
        .join(EddyCovarianceStationInfo, isouter=True)
    )
    result = session.execute(stmt).one_or_none()
    if result is None:
        raise HTTPException(status_code=404, detail="Station not found")

    name = result[0]

    kind = None
    if result[1] is not None:
        kind = StationKind.METEO
    elif result[2] is not None:
        kind = StationKind.EDDY_COVARIANCE

    return {"id": station_id, "name": name, "kind": kind}


class StationSnapshotInfo(TypedDict):
    id: int
    name: str
    location: GeographicPosition
    count: int


class StationsSnapshot(TypedDict):
    stations: list[StationSnapshotInfo]


@app.get(
    "/stations/snapshot",
    summary="Retrieve a list of stations with valid data for the given variable and time range",
)
def get_stations_snapshot(
    variable: str,
    session: SessionDep,
    since: datetime | None = None,
    until: datetime | None = None,
) -> StationsSnapshot:
    logger.info("GET /stations/snapshot")

    md = [var for var in variable_metadata if var.column_name == variable]
    if len(md) == 0:
        raise HTTPException(400, detail=f"Invalid variable name: {variable}")

    md = md[0]
    table = measurements_tables[md.table_name]
    table_column = table.__table__.columns[md.column_name]

    if since is None or until is None:
        # Find the latest timestamp for this variable
        stmt = select(func.max(table.timestamp)).where(table_column.is_not(None))
        latest_timestamp = session.execute(stmt).scalar_one_or_none()

        if latest_timestamp is None:
            raise HTTPException(
                status_code=404,
                detail="No measurements found in the database for the given variable (ignoring datetime constraints)",
            )

        if since is None:
            since = latest_timestamp - timedelta(hours=12)

        if until is None:
            until = latest_timestamp

    if since >= until:
        raise HTTPException(
            status_code=400, detail="`since` date cannot be after `until` date"
        )

    duration = until - since
    max_days = 7
    if duration >= timedelta(days=max_days):
        raise HTTPException(
            status_code=400,
            detail=f"We currently don't allow retrieving data for periods of longer than {max_days} days in a single request",
        )

    count_subq = (
        select(func.count(table.timestamp))
        .where(
            table.station_id == Station.id,
            table_column.is_not(None),
            since <= table.timestamp,
            table.timestamp <= until,
        )
        .scalar_subquery()
    )

    stmt = select(Station.id, Station.name, Station.location, count_subq).where(
        count_subq > 0
    )
    results = session.execute(stmt).all()

    stations = []
    for row in results:
        location = to_shape(row[2])
        assert isinstance(location, Point), "Station location is not a single point"

        location = GeographicPosition(latitude=location.x, longitude=location.y)

        stations = [
            StationSnapshotInfo(id=row[0], name=row[1], location=location, count=row[3])
            for row in results
        ]

    return StationsSnapshot(stations=stations)


@app.get(
    "/stations/{station_id}",
    summary="Retrieve more information and monitored variables list for a station",
    responses={
        404: {"description": "Station with the given ID was not found in the database"}
    },
)
def get_station_info(station_id: int, session: SessionDep) -> StationInformation:
    logger.info("GET /stations/%d", station_id)

    stmt = (
        select(
            Station.name,
            Station.location,
            MeteorologicalStationInfo.code,
            EddyCovarianceStationInfo.code,
        )
        .where(Station.id == station_id)
        .join(MeteorologicalStationInfo, isouter=True)
        .join(EddyCovarianceStationInfo, isouter=True)
    )
    result = session.execute(stmt).one_or_none()
    if result is None:
        raise HTTPException(status_code=404, detail="Station not found")

    name = result[0]
    location = to_shape(result[1])
    assert isinstance(location, Point), "Station location is not a single point"

    kind: StationKind | None = None
    variables: list[VariableEntry] = []
    if result[2] is not None:
        kind = StationKind.METEO
        variables = meteo_variables_entries
    elif result[3] is not None:
        kind = StationKind.EDDY_COVARIANCE
        variables = eddy_covariance_variables_entries

    location = GeographicPosition(latitude=location.x, longitude=location.y)
    station = StationInformation(
        id=station_id, name=name, kind=kind, location=location, variables=variables
    )

    return station


class Measurements(TypedDict):
    variables: list[str]
    timestamps: dict[str, list[str]]
    measurements: dict[str, list[float | None]]


@app.get(
    "/stations/{station_id}/measurements",
    summary="Fetch recorded measurements from a station",
)
def get_station_measurements(
    station_id: int,
    session: SessionDep,
    variables: Annotated[list[str], Query()] = [],
    since: datetime | None = None,
    until: datetime | None = None,
) -> Measurements:
    logger.info("GET /stations/%d/measurements", station_id)

    station = get_station_by_id(session, station_id)
    if station["kind"] is None:
        raise HTTPException(
            status_code=400,
            detail="Cannot get measurements for a station of unknown kind",
        )

    logger.info("Station kind: %s", station["kind"])

    if station["kind"] == StationKind.METEO:
        valid_variables = [var.column_name for var in meteo_variables]
    elif station["kind"] == StationKind.EDDY_COVARIANCE:
        valid_variables = [var.column_name for var in eddy_covariance_variables]
    else:
        raise NotImplementedError(f"unsupported station kind: '{station['kind']}'")

    if variables:
        invalid_variables = set(variables) - set(valid_variables)
        if invalid_variables:
            raise HTTPException(
                400,
                detail=f"Some of the requested variables are not available for this station kind: {', '.join(invalid_variables)}",
            )
    else:
        # By default, retrieve all available variables
        variables = valid_variables

    variables_set = set(variables)

    if not variables_set:
        raise HTTPException(
            status_code=400, detail="No valid variables selected for fetch"
        )

    metadata = [md for md in variable_metadata if md.column_name in variables_set]

    md = metadata[0]
    table = measurements_tables[md.table_name]
    stmt = select(func.max(table.timestamp)).where(
        table.station_id == station_id,
        table.__table__.columns[md.column_name].is_not(None),
    )
    latest_timestamp = session.execute(stmt).scalar_one_or_none()

    if latest_timestamp is None:
        raise HTTPException(
            status_code=404,
            detail="No measurements found in the database for the given station and variables list",
        )

    if since is None:
        since = latest_timestamp - timedelta(hours=2)

    if until is None:
        until = latest_timestamp

    if since >= until:
        raise HTTPException(
            status_code=400, detail="`since` date cannot be after `until` date"
        )

    duration = until - since
    max_days = 7
    if duration >= timedelta(days=max_days):
        raise HTTPException(
            status_code=400,
            detail=f"We currently don't allow retrieving data for periods of longer than {max_days} days in a single request",
        )

    timestamps: dict[str, list[str]] = {}
    measurements: dict[str, list[float | None]] = {}
    # TODO: use a single query to load all the measurements at once
    for md in metadata:
        variable_name = md.column_name

        table = measurements_tables[md.table_name]
        if station["kind"] == StationKind.METEO:
            stmt = (
                select(table.timestamp, table.__table__.columns[md.column_name])
                .where(
                    table.station_id == station_id,
                    since <= table.timestamp,
                    table.timestamp <= until,
                )
                .order_by(asc(table.timestamp))
                .limit(50_000)
            )
        else:
            subq = (
                select(
                    table.timestamp,
                    table.__table__.columns[md.column_name],
                    func.row_number()
                    .over(order_by=asc(table.timestamp))
                    .label("row_number"),
                )
                .where(
                    table.station_id == station_id,
                    since <= table.timestamp,
                    table.timestamp <= until,
                )
                .order_by(asc(table.timestamp))
                .subquery()
            )
            stmt = (
                select(subq.c["timestamp"], subq.c[md.column_name])
                .where(subq.c.row_number % 50 == 0)
                .limit(50_000)
            )

        rows = session.execute(stmt).all()

        logger.info(
            "Querying variable %s; retrieved %d rows from DB", variable_name, len(rows)
        )

        timestamps[variable_name] = [row[0].isoformat() for row in rows]
        measurements[variable_name] = [
            (float(row[1]) if row[1] is not None else None) for row in rows
        ]

    logger.info("Finished loading measurements from DB")

    return {
        "variables": variables,
        "timestamps": timestamps,
        "measurements": measurements,
    }


# TODO: reconsider the design of the endpoint
@app.get("/measurements", summary="Fetch all measurements matching some criteria")
def get_measurements(
    session: SessionDep,
    since: datetime,
    until: datetime,
    variables: Annotated[list[str], Query()],
    station_ids: Annotated[list[str], Query()] = [],
) -> Measurements:
    logger.info("GET /measurements")

    if since >= until:
        raise HTTPException(
            status_code=400, detail="`since` date cannot be after `until` date"
        )

    duration = until - since
    max_hours = 6
    if duration >= timedelta(hours=max_hours):
        raise HTTPException(
            status_code=400,
            detail=f"We currently don't allow retrieving data for periods of longer than {max_hours} hours in a single request",
        )

    # Check validity of requested variables
    valid_variables = [var.column_name for var in variable_metadata]

    variables_set = set(variables)
    invalid_variables = variables_set - set(valid_variables)
    if invalid_variables:
        raise HTTPException(
            400,
            detail=f"Some of the requested variables were not recognized: {', '.join(invalid_variables)}",
        )

    if not variables_set:
        raise HTTPException(
            status_code=400, detail="No valid variables selected for fetch"
        )

    valid_station_ids = session.execute(select(distinct(Station.id))).scalars().all()

    station_ids_set = set(station_ids)
    valid_station_ids_set = set(valid_station_ids)
    invalid_station_ids = station_ids_set - valid_station_ids_set
    if invalid_station_ids:
        raise HTTPException(
            400,
            detail=f"Some of the requested stations were not recognized: {', '.join(invalid_station_ids)}",
        )

    if not station_ids_set:
        # By default, consider all stations
        station_ids_set = valid_station_ids_set

    metadata = [md for md in variable_metadata if md.column_name in variables_set]

    timestamps: dict[str, list[str]] = {}
    measurements: dict[str, list[float | None]] = {}

    # TODO: use a single query to load all the measurements at once
    for md in metadata:
        table = measurements_tables[md.table_name]
        variable_name = md.column_name

        if md.table_name.startswith("meteorological"):
            stmt = (
                select(table.timestamp, table.__table__.columns[md.column_name])
                .where(
                    table.station_id.in_(station_ids_set),
                    since <= table.timestamp,
                    table.timestamp >= until,
                )
                .order_by(asc(table.timestamp))
                .limit(20_000)
            )

        else:
            subq = (
                select(
                    table.timestamp,
                    table.__table__.columns[md.column_name],
                    func.row_number()
                    .over(partition_by=table.station_id, order_by=asc(table.timestamp))
                    .label("row_number"),
                )
                .where(
                    table.station_id.in_(station_ids_set),
                    since <= table.timestamp,
                    table.timestamp <= until,
                )
                .order_by(asc(table.timestamp))
                .subquery()
            )
            stmt = (
                select(subq.c["timestamp"], subq.c[md.column_name])
                .where(subq.c.row_number % 50 == 0)
                .limit(20_000)
            )

        start_time = perf_counter()
        rows = session.execute(stmt).all()
        end_time = perf_counter()
        duration = end_time - start_time

        logger.info(
            "Querying variable %s took %.3f seconds; retrieved %d rows from DB",
            variable_name,
            duration,
            len(rows),
        )

        timestamps[variable_name] = [row[0].isoformat() for row in rows]
        measurements[variable_name] = [
            (float(row[1]) if row[1] is not None else None) for row in rows
        ]

    logger.info("Finished loading measurements from DB")

    return {
        "variables": variables,
        "timestamps": timestamps,
        "measurements": measurements,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="localhost", port=8000)
