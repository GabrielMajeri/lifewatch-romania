from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from csv import DictReader
from datetime import datetime, timedelta
from enum import StrEnum
import logging
from os import getenv
from pathlib import Path
import re
from typing import Annotated, Literal, Sequence, TypedDict, cast

import coloredlogs
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import asc, create_engine, func, select
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


app = FastAPI(lifespan=lifespan)


def get_session() -> Generator[Session, None, None]:
    "Constructs and yields a new SQLAlchemy `Session`."
    with Session(engine) as session:
        yield session


type SessionDep = Annotated[Session, Depends(get_session)]


class HealthStatusResponse(TypedDict):
    status: Literal["healthy"]


@app.get("/health")
def health() -> HealthStatusResponse:
    return {"status": "healthy"}


class StationKind(StrEnum):
    METEO = "meteorological"
    EDDY_COVARIANCE = "eddy-covariance"


class StationEntry(TypedDict):
    id: int
    name: str
    kind: StationKind | None


@app.get("/stations")
def get_stations(session: SessionDep) -> Sequence[StationEntry]:
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


class StationInformation(StationEntry):
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


@app.get(
    "/stations/{station_id}",
    responses={
        404: {"description": "Station with the given ID was not found in the database"}
    },
)
def get_station_info(station_id: int, session: SessionDep) -> StationInformation:
    station = get_station_by_id(session, station_id)

    kind = station["kind"]
    variables: list[VariableEntry] = []
    if kind == StationKind.METEO:
        variables = meteo_variables_entries
    elif kind == StationKind.EDDY_COVARIANCE:
        variables = eddy_covariance_variables_entries

    station = cast(StationInformation, station)
    station["variables"] = variables

    return station


class Measurements(TypedDict):
    variables: list[str]
    timestamps: dict[str, list[str]]
    measurements: dict[str, list[float | None]]


@app.get("/stations/{station_id}/measurements")
def get_measurements(
    station_id: int,
    session: SessionDep,
    variables: Annotated[list[str], Query()] = [],
    since: datetime | None = None,
    until: datetime | None = None,
) -> Measurements:
    station = get_station_by_id(session, station_id)
    if station["kind"] is None:
        raise HTTPException(
            status_code=400,
            detail="Cannot get measurements for a station of unknown kind",
        )

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
    max_hours = 12
    if duration >= timedelta(hours=max_hours):
        raise HTTPException(
            status_code=400,
            detail=f"We currently don't allow retrieving data for periods of longer than {max_hours} hours in a single request",
        )

    timestamps: dict[str, list[str]] = {}
    measurements: dict[str, list[float | None]] = {}
    # TODO: use a single query to load all the measurements at once
    for md in metadata:
        variable_name = md.column_name

        table = measurements_tables[md.table_name]
        stmt = (
            select(table.timestamp, table.__table__.columns[md.column_name])
            .where(
                table.station_id == station_id,
                since <= table.timestamp,
                table.timestamp <= until,
            )
            .order_by(asc(table.timestamp))
        )
        rows = session.execute(stmt).all()

        timestamps[variable_name] = [row[0].isoformat() for row in rows]
        measurements[variable_name] = [
            (float(row[1]) if row[1] is not None else None) for row in rows
        ]

    return {
        "variables": variables,
        "timestamps": timestamps,
        "measurements": measurements,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="localhost", port=8000)
