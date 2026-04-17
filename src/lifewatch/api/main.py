from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from csv import DictReader
from enum import StrEnum
import logging
from os import getenv
from pathlib import Path
import re
from typing import Annotated, Literal, Sequence, TypedDict

import coloredlogs
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from lifewatch.models import (
    EddyCovarianceStationInfo,
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

meteo_variable_table_name_pattern = re.compile(
    r"^meteorological_\d+_second_measurements$"
)
meteo_variables = [
    md
    for md in variable_metadata
    if meteo_variable_table_name_pattern.match(md.table_name)
]
eddy_covariance_variables = [
    md for md in variable_metadata if md.table_name == "eddy_covariance_measurements"
]


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


@app.get(
    "/stations/{station_id}",
    responses={
        404: {"description": "Station with the given ID was not found in the database"}
    },
)
def get_station_info(station_id: int, session: SessionDep) -> StationInformation:
    stmt = (
        select(
            Station.id,
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

    id = result[0]
    name = result[1]

    kind = None
    variables: list[VariableEntry] = []
    if result[2] is not None:
        kind = StationKind.METEO
        variables = meteo_variables_entries
    elif result[3] is not None:
        kind = StationKind.EDDY_COVARIANCE
        variables = eddy_covariance_variables_entries

    return {"id": id, "name": name, "kind": kind, "variables": variables}
