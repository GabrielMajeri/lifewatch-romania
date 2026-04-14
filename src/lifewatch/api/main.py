from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from enum import StrEnum
import logging
from os import getenv
from typing import Annotated, Literal, Sequence, TypedDict

import coloredlogs
from dotenv import load_dotenv
from fastapi import Depends, FastAPI
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
