import datetime
from pathlib import Path
import logging
from typing import Annotated

import coloredlogs
from dotenv import load_dotenv
from paramiko import Ed25519Key, PKey
from rich.console import Console
from sqlalchemy import create_engine
import typer

from lifewatch.sftp import SFTPCredentials

from ..ftp import FTPCredentials
from ..ingestion.eddy_covariance import EddyCovarianceDataFilters
from ..ingestion.meteo import (
    MeteorologicalDataFilters,
    MeteorologicalVariablePeriodicity,
)

from .utils import AliasGroup

# Load environment variables from `.env` file (if it exists)
load_dotenv()

console = Console()

coloredlogs.install(level=logging.INFO)
logger = logging.getLogger(__name__)

app = typer.Typer(cls=AliasGroup)


@app.command(
    "meteorological | meteo | m",
    help="Ingest meteorological data files from a given path (file or directory), or directly from the LifeWatch meteorological data FTP server.",
)
def meteo(
    file_or_directory_path: Annotated[
        Path | None,
        typer.Argument(
            help="Path to a file to ingest or directory containing data files to be read and ingested."
        ),
    ] = None,
    ftp_host: Annotated[str | None, typer.Option(envvar="METEO_FTP_HOST")] = None,
    ftp_port: Annotated[int | None, typer.Option(envvar="METEO_FTP_PORT")] = None,
    ftp_user: Annotated[str | None, typer.Option(envvar="METEO_FTP_USER")] = None,
    ftp_password: Annotated[
        str | None, typer.Option(envvar="METEO_FTP_PASSWORD")
    ] = None,
    database_url: Annotated[
        str,
        typer.Option(envvar="DATABASE_URL"),
    ] = "postgresql+psycopg://dev:pwd@localhost:5432/lifewatch",
    selected_periodicities: Annotated[
        list[MeteorologicalVariablePeriodicity],
        typer.Option(
            help="Periodicities of variables which to ingest. Can be used to filter out certain groups of variables/measurements."
        ),
    ] = list(MeteorologicalVariablePeriodicity),
    selected_station_codes: Annotated[
        list[str],
        typer.Option(
            "--station",
            "--station-code",
            "--station-id",
            help="Short identification code of weather stations for which to retrieve data. If not set, will fetch measurements from all available stations. Example value: `AG-Dmb`.",
        ),
    ] = [],
    from_date_str: Annotated[str, typer.Option("--from", "--since")] = "",
    until_date_str: Annotated[str, typer.Option("--until")] = "",
    debug: Annotated[
        bool,
        typer.Option(
            help="Enable debug-level logging, for diagnosing issues with the FTP connection or data ingestion code."
        ),
    ] = False,
) -> None:
    if debug:
        coloredlogs.adjust_level(logger, logging.DEBUG)

    if from_date_str:
        from_date = datetime.date.fromisoformat(from_date_str)
    else:
        from_date = None

    if until_date_str:
        until_date = datetime.date.fromisoformat(until_date_str)
    else:
        until_date = None

    filters = MeteorologicalDataFilters(
        periodicities=selected_periodicities,
        station_codes=selected_station_codes,
        from_date=from_date,
        until_date=until_date,
    )

    logger.info("LifeWatch meteorological data ingestion tool started")

    # Connect to the database
    engine = create_engine(database_url)

    if file_or_directory_path is not None:
        logger.info("Path provided, running in local mode")

        from ..ingestion.meteo import ingest_meteo_data_from_local_path

        ingest_meteo_data_from_local_path(file_or_directory_path, filters, engine)

    else:
        logger.info("Path not provided, running in FTP (remote) mode")

        from ..ingestion.meteo import ingest_meteo_data_from_ftp_server

        if not ftp_host:
            logger.error("FTP server host must be provided when running in FTP mode")
            raise typer.Exit(1)

        if not ftp_port:
            logger.error("FTP server port must be provided when running in FTP mode")
            raise typer.Exit(1)

        if not ftp_user:
            logger.error("FTP username must be provided when running in FTP mode")
            raise typer.Exit(1)

        if not ftp_password:
            logger.error("FTP user password must be provided when running in FTP mode")
            raise typer.Exit(1)

        ftp_credentials = FTPCredentials(ftp_host, ftp_port, ftp_user, ftp_password)

        ingest_meteo_data_from_ftp_server(ftp_credentials, filters, engine)


@app.command(
    "eddy-covariance | ec",
    help="Ingest eddy covariance data files from a given path (file or directory), or directly from the LifeWatch data collection SFTP server.",
)
def eddy_covariance(
    file_or_directory_path: Annotated[
        Path | None,
        typer.Argument(
            help="Path to a file or directory containing data to be read and ingested."
        ),
    ] = None,
    sftp_host: Annotated[str | None, typer.Option(envvar="EC_SFTP_HOST")] = None,
    sftp_port: Annotated[int | None, typer.Option(envvar="EC_SFTP_PORT")] = 22,
    sftp_user: Annotated[str | None, typer.Option(envvar="EC_SFTP_USER")] = None,
    sftp_password: Annotated[
        str | None, typer.Option(envvar="EC_SFTP_PASSWORD")
    ] = None,
    sftp_private_key_path: Annotated[
        Path | None, typer.Option(envvar="EC_SFTP_PRIVATE_KEY_PATH")
    ] = None,
    sftp_private_key_password: Annotated[
        str | None, typer.Option(envvar="EC_SFTP_PRIVATE_KEY_PASSWORD")
    ] = None,
    sftp_base_path: Annotated[str | None, typer.Option()] = "sftpuser/icos",
    database_url: Annotated[
        str,
        typer.Option(envvar="DATABASE_URL"),
    ] = "postgresql+psycopg://dev:pwd@localhost:5432/lifewatch",
    selected_station_codes: Annotated[
        list[str],
        typer.Option(
            "--station",
            "--station-code",
            "--station-id",
            help="Short identification code of eddy covariance stations for which to retrieve data. If not set, will fetch measurements from all available stations. Example value: `01014` (for Brașov station).",
        ),
    ] = [],
    from_timestamp_str: Annotated[str, typer.Option("--from", "--since")] = "",
    until_timestamp_str: Annotated[str, typer.Option("--until")] = "",
    debug: Annotated[
        bool,
        typer.Option(
            help="Enable debug-level logging, for diagnosing issues with the FTP connection or data ingestion code."
        ),
    ] = False,
) -> None:
    if debug:
        coloredlogs.adjust_level(logger, logging.DEBUG)

    if from_timestamp_str:
        from_timestamp = datetime.datetime.fromisoformat(from_timestamp_str)
    else:
        from_timestamp = None

    if until_timestamp_str:
        until_timestamp = datetime.datetime.fromisoformat(until_timestamp_str)
    else:
        until_timestamp = None

    filters = EddyCovarianceDataFilters(
        station_codes=selected_station_codes,
        from_timestamp=from_timestamp,
        until_timestamp=until_timestamp,
    )

    logger.info("LifeWatch eddy covariance data ingestion tool started")

    # Connect to the database
    engine = create_engine(database_url)

    if file_or_directory_path is not None:
        logger.info("Path provided, running in local mode")

        from ..ingestion.eddy_covariance import (
            ingest_eddy_covariance_data_from_local_path,
        )

        ingest_eddy_covariance_data_from_local_path(
            file_or_directory_path, filters, engine
        )

    else:
        logger.info("Path not provided, running in FTP (remote) mode")

        from ..ingestion.eddy_covariance import (
            ingest_eddy_covariance_data_from_sftp_server,
        )

        if not sftp_host:
            logger.error("SFTP server host must be provided when running in SFTP mode")
            raise typer.Exit(1)

        if not sftp_port:
            logger.error("SFTP server port must be provided when running in SFTP mode")
            raise typer.Exit(1)

        if not sftp_user:
            logger.error("SFTP username must be provided when running in SFTP mode")
            raise typer.Exit(1)

        if not sftp_password and not sftp_private_key_path:
            logger.error(
                "SFTP password or private key must be provided when running in SFTP mode"
            )
            raise typer.Exit(1)

        sftp_private_key: PKey | None = None
        if sftp_private_key_path:
            # TODO: support other key types
            sftp_private_key = Ed25519Key.from_private_key_file(
                sftp_private_key_path, sftp_private_key_password
            )

        sftp_credentials = SFTPCredentials(
            sftp_host, sftp_port, sftp_user, sftp_password, sftp_private_key
        )

        ingest_eddy_covariance_data_from_sftp_server(
            sftp_credentials, filters, engine, sftp_base_path
        )
