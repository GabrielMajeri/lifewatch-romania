import base64
import datetime
from pathlib import Path
import logging
import re
from stat import S_ISREG
from tempfile import gettempdir
from typing import Annotated

import coloredlogs
from dotenv import load_dotenv
from paramiko import Ed25519Key, PKey, SFTPClient, SSHClient
from rich.console import Console
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session
import typer

from ..ftp import FTPCredentials
from ..ingestion.meteo import (
    MeteorologicalDataFilters,
    MeteorologicalVariablePeriodicity,
)
from ..models import (
    MeteorologicalStationInfo,
    Station,
)

# Load environment variables from `.env` file (if it exists)
load_dotenv()

console = Console()

coloredlogs.install(level=logging.INFO)
logger = logging.getLogger(__name__)

app = typer.Typer()


@app.command(
    help="Ingest meteorological data files from a given path (file or directory), or directly from the LifeWatch meteorological data FTP server."
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

    logger.info("LifeWatch data ingestion tool started")

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


@app.command(help="Import eddy covariance files from a local directory.")
def local_eddy_covariance(
    station_name: str,
    input_directory: Path,
    database_url: Annotated[
        str,
        typer.Argument(envvar="DATABASE_URL"),
    ] = "postgresql+psycopg://dev:pwd@localhost:5432/lifewatch",
):
    engine = create_engine(database_url)

    with Session(engine) as session:
        station_id = session.scalar(
            select(Station.id).where(Station.name == station_name)
        )
        if station_id is None:
            raise Exception(f"Could not find sensor station with name '{station_name}'")

    # Go through all of the files in the input directory
    if not input_directory.exists():
        raise FileNotFoundError(f"Directory does not exist: '{input_directory}'")

    if not input_directory.is_dir():
        raise Exception(
            f"Path points to a file, not to a directory: '{input_directory}'"
        )

    console.print("[blue]Starting to process GHG files...[/blue]")

    from ..ingestion.eddy_covariance import import_eddy_covariance_data_from_ghg_file

    file_counter = 0
    for entry_path in input_directory.iterdir():
        if entry_path.is_file() and entry_path.suffix == ".ghg":
            console.print(f"  Processing '{entry_path}'...")
            import_eddy_covariance_data_from_ghg_file(engine, station_id, entry_path)
            file_counter += 1

    console.print(f"[blue]Processed {file_counter} GHG files[/blue]")


@app.command(help="Import files from a directory on a remote machine, over SFTP.")
def remote_eddy_covariance(
    station_name: str,
    private_key_path: Annotated[
        Path, typer.Argument(envvar="DATABASE_SSH_PRIVATE_KEY")
    ],
    database_url: Annotated[
        str,
        typer.Argument(envvar="DATABASE_URL"),
    ] = "postgresql+psycopg://dev:pwd@localhost:5432/lifewatch",
    sftp_path: Annotated[
        str | None,
        typer.Option(
            help="Path to the input directory on the SFTP share. If not set, will try to read from the database (from the station's metadata)."
        ),
    ] = None,
) -> None:
    engine = create_engine(database_url)

    from ..ingestion.eddy_covariance import import_eddy_covariance_data_from_ghg_file

    with Session(engine) as session:
        station_id = session.scalar(
            select(Station.id).where(Station.name == station_name)
        )
        if station_id is None:
            raise Exception(f"Could not find sensor station with name '{station_name}'")

        if not sftp_path:
            sftp_path = session.scalar(
                select(Station.extra["sftp_path"]).where(Station.id == station_id)
            )
            if sftp_path is None:
                raise Exception(
                    "`sftp_path` is not set and it couldn't be read from the database"
                )
            else:
                console.print(
                    f"Successfully read `sftp_path` from database: '{sftp_path}'"
                )

    console.print(
        f"[blue]Connecting to remote server {DATA_COLLECTION_SERVER_HOSTNAME} over SSH...[/blue]"
    )

    with DataCollectorClient(private_key_path) as client:
        client.sftp_client.chdir(sftp_path)

        console.print("[blue]Starting to process GHG files...[/blue]")

        try:
            # Generate a local temporary file path
            temp_dir = gettempdir()
            local_dir = Path(temp_dir) / "eddy_covariance"
            local_dir.mkdir(parents=True, exist_ok=True)

            file_counter = 0
            file_attrs = client.sftp_client.listdir_attr()
            for entry in file_attrs:
                console.print(f"  Checking '{entry.filename}'...")
                mode = entry.st_mode
                if not mode:
                    # Skip this entry if we cannot determine its type
                    continue

                is_file = S_ISREG(mode)
                if is_file and entry.filename.endswith(".ghg"):
                    console.print(f"  Processing '{entry.filename}'...")

                    remote_path = entry.filename

                    local_path = local_dir / entry.filename

                    try:
                        client.sftp_client.get(remote_path, local_path)
                        import_eddy_covariance_data_from_ghg_file(
                            engine, station_id, local_path
                        )
                    finally:
                        # Try to remove the local copy of the file, if possible
                        local_path.unlink(missing_ok=True)

                    file_counter += 1

        finally:
            # Try to remove the `eddy_covariance` directory, if possible
            try:
                local_dir.rmdir()
            except Exception:
                pass

        console.print(f"[blue]Processed {file_counter} GHG files[/blue]")


DATA_COLLECTION_SERVER_HOSTNAME: str = "colectare.lifewatch.unibuc.ro"


class DataCollectorClient:
    "Client for connecting to the data collection machine and reading data files from it."

    ssh_client: SSHClient
    sftp_client: SFTPClient

    def __init__(self, private_key_path: Path) -> None:
        self.ssh_client = SSHClient()

        # Add the data collection server's host key to the SSH client's known host keys
        host_keys = self.ssh_client.get_host_keys()
        data_collection_server_host_key_type = "ssh-ed25519"
        data_collection_server_host_key = (
            "AAAAC3NzaC1lZDI1NTE5AAAAIPWFvNEtCG19tm8A7BPl4gT54IJcJLFjpkyZBiEDsMAZ"
        )
        host_keys.add(
            DATA_COLLECTION_SERVER_HOSTNAME,
            data_collection_server_host_key_type,
            PKey.from_type_string(
                data_collection_server_host_key_type,
                base64.decodebytes(data_collection_server_host_key.encode()),
            ),
        )

        # Try to connect
        self.ssh_client.connect(
            DATA_COLLECTION_SERVER_HOSTNAME,
            username="sftpuser",
            pkey=Ed25519Key.from_private_key_file(private_key_path),
        )

        self.sftp_client = self.ssh_client.open_sftp()
        self.sftp_client.chdir("sftpuser")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.sftp_client.close()
        self.ssh_client.close()


EDDY_COVARIANCE_DATA_FILE_NAME_REGEXP = re.compile(r"^.*_smart3-\d+\.data$")

ghg_file_column_name_to_table_column_name_mapping = None
