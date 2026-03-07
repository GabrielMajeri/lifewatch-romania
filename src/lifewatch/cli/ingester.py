import base64
import csv
from dataclasses import dataclass
import datetime
from enum import StrEnum
from io import BytesIO, StringIO, TextIOWrapper
from pathlib import Path
import logging
import re
from stat import S_ISREG
from tempfile import gettempdir
from time import perf_counter
from typing import IO, Annotated
from zipfile import ZipFile

import coloredlogs
from dotenv import load_dotenv
from paramiko import Ed25519Key, PKey, SFTPClient, SSHClient
from rich.console import Console
from sqlalchemy import Engine, create_engine, literal, select
from sqlalchemy.orm import Session
import typer

from ..ftp import Fixed_FTP_TLS
from ..models import (
    EddyCovarianceMeasurement,
    Meteorological20SecondMeasurement,
    MeteorologicalStationInfo,
    Station,
)

# Load environment variables from `.env` file (if it exists)
load_dotenv()

console = Console()

coloredlogs.install(level=logging.INFO)
logger = logging.getLogger(__name__)

app = typer.Typer()


class MeteorologicalVariablePeriodicity(StrEnum):
    EVERY_20_SECONDS = "20s"
    EVERY_30_SECONDS = "30s"
    EVERY_60_SECONDS = "60s"


@app.command(help="Ingest meteorological data files from the FTP server.")
def meteo(
    ftp_host: Annotated[str, typer.Argument(envvar="METEO_FTP_HOST")],
    ftp_port: Annotated[int, typer.Argument(envvar="METEO_FTP_PORT")],
    ftp_user: Annotated[str, typer.Argument(envvar="METEO_FTP_USER")],
    ftp_password: Annotated[str, typer.Argument(envvar="METEO_FTP_PASSWORD")],
    database_url: Annotated[
        str,
        typer.Argument(envvar="DATABASE_URL"),
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

    # Connect to the database
    engine = create_engine(database_url)

    logger.info("LifeWatch data ingestion tool started")

    logger.info("Connecting to FTP server `%s:%d`...", ftp_host, ftp_port)

    with Fixed_FTP_TLS() as ftp:
        response = ftp.connect(ftp_host, ftp_port, timeout=3)
        logger.debug("FTP connect response: %s", response)

        response = ftp.auth()
        logger.debug("FTP auth response: %s", response)

        response = ftp.login(ftp_user, ftp_password)
        logger.debug("FTP login response: %s", response)

        response = ftp.prot_p()
        logger.debug("FTP protected protocol switch response: %s", response)

        root_dir_entries = ftp.nlst()
        logger.debug("Root directory entries: %s", root_dir_entries)

        # TODO: in the future we might want to / have to support reading the "raw" data files directly.
        # They're still CSV files, but the first few rows act as a header (describing station/sensor metadata, measurement units, column names etc).
        processed_data_dir = "meteo_procesat"
        if processed_data_dir not in root_dir_entries:
            raise RuntimeError(
                f"Processed meteorological data directory '{processed_data_dir}' not found in FTP share root"
            )

        response = ftp.cwd(processed_data_dir)
        logger.debug("FTP change current directory response: %s", response)

        processed_data_dir_entries = ftp.nlst()
        logger.debug("Processed data files: %s", processed_data_dir_entries)

        logger.info(
            "Selected variable periodicities for which to ingest data: %s",
            ", ".join(p.value for p in selected_periodicities),
        )

        # Construct a lookup table from station codes to station IDs
        stmt = select(
            MeteorologicalStationInfo.station_id,
            MeteorologicalStationInfo.code,
        )

        # If we'll filter the data files by station code,
        # only look up associations for the stations with those codes.
        if selected_station_codes:
            stmt = stmt.where(
                MeteorologicalStationInfo.code.in_(selected_station_codes)
            )

        with Session(engine) as session:
            result = session.execute(stmt)
            station_ids_and_codes = result.all()

        if selected_station_codes and len(station_ids_and_codes) != len(
            selected_station_codes
        ):
            logger.error(
                "Could not find stations corresponding to some of the provided station codes"
            )

            retrieved_station_codes = (code for (_, code) in station_ids_and_codes)
            missing_stations = set(selected_station_codes) - set(
                retrieved_station_codes
            )
            logger.error(
                "Station codes for which no corresponding station could be found in the database: %s",
                " ".join(missing_stations),
            )

            raise typer.Exit(1)

        station_code_to_station_id: dict[str, int] = {
            station_code: station_id
            for (station_id, station_code) in station_ids_and_codes
        }

        if MeteorologicalVariablePeriodicity.EVERY_20_SECONDS in selected_periodicities:
            if "20" not in processed_data_dir_entries:
                raise RuntimeError(
                    "Directory for variables with periodicity of 20 seconds not found"
                )

            response = ftp.cwd("20")
            logger.debug("FTP change current directory response: %s", response)

            data_files_20s_periodicity = ftp.nlst()
            # logger.debug("20 seconds directory listing: %s", data_files_20s_periodicity)

            # Extract station identifiers and file dates
            files_metadata = extract_metadata_from_file_names(
                data_files_20s_periodicity
            )

            # Filter files based on user-provided criteria
            files_metadata = filter_files(
                files_metadata, selected_station_codes, from_date, until_date
            )

            if not files_metadata:
                logger.error("No data files were found matching the provided filters")
                raise typer.Exit(1)

            logger.info(
                "Found %d files matching provided filters",
                len(files_metadata),
            )

            station_ids = []
            for meta in files_metadata:
                station_code = meta.station_code
                station_id = station_code_to_station_id.get(station_code)

                if not station_id:
                    logger.error(
                        f"Could not find station with code '{station_code}' in list of stations retrieved from the database"
                    )
                    raise typer.Exit(1)

                station_ids.append(station_id)

            logger.info("Beginning to read/ingest files")

            for file_meta, station_id in zip(files_metadata, station_ids):
                buffer = BytesIO()
                response = ftp.retrbinary(f"RETR {file_meta.name}", buffer.write)
                logger.debug("FTP retrieve file response: %s", response)

                file_contents = buffer.getvalue().decode("utf-8")
                string_io = StringIO(file_contents)

                ingest_20_seconds_periodicity_weather_data(
                    string_io, engine, station_id
                )

        return

        if "30" not in processed_data_dir_entries:
            raise RuntimeError(
                "Directory for variables with periodicity of 30 seconds not found"
            )

        response = ftp.cwd("../30")

        data_files_30s_periodicity = ftp.nlst()
        logger.info("30 seconds directory listing: %s", data_files_30s_periodicity)

        if "60" not in processed_data_dir_entries:
            raise RuntimeError(
                "Directory for variables with periodicity of 60 seconds not found"
            )

        response = ftp.cwd("../60")

        data_files_60s_periodicity = ftp.nlst()
        logger.info("60 seconds directory listing: %s", data_files_60s_periodicity)

    raise NotImplementedError("Not yet working")


@dataclass
class MeteorologicalFileMeta:
    name: str
    station_code: str
    date: datetime.date


STATION_CODE_PATTERN = r"(?P<station_code>\w{2}-\w{3})"
PROCESSED_METEO_FILE_DATE_PATTERN = r"(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})"


def extract_metadata_from_file_names(
    file_names: list[str],
) -> list[MeteorologicalFileMeta]:
    "Extract metadata (station code and record date) from a list of meteorological data file names."

    file_name_pattern = re.compile(
        f"^{STATION_CODE_PATTERN}_BM_{PROCESSED_METEO_FILE_DATE_PATTERN}"
    )

    metadata: list[MeteorologicalFileMeta] = []
    for file_name in file_names:
        match = file_name_pattern.match(file_name)

        # File does not match (probably not a data file)
        if not match:
            continue

        station_code = str(match.group("station_code"))

        # Extract the file's date component
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        file_date = datetime.date(year, month, day)

        meta = MeteorologicalFileMeta(
            name=file_name,
            station_code=station_code,
            date=file_date,
        )
        metadata.append(meta)

    return metadata


def filter_files(
    file_metas: list[MeteorologicalFileMeta],
    station_codes: list[str] | None,
    from_date: datetime.date | None,
    until_date: datetime.date | None,
) -> list[MeteorologicalFileMeta]:
    "Filter a list of weather station data files using the provided criteria."

    filter_by_date = from_date is not None or until_date is not None

    # If no filter criterion is provided, return all files
    if not station_codes and not filter_by_date:
        return file_metas

    if station_codes:
        allowed_station_codes = set(station_codes)

    filtered_files: list[MeteorologicalFileMeta] = []

    for meta in file_metas:
        if station_codes:
            if meta.station_code not in allowed_station_codes:
                continue

        if from_date is not None and meta.date < from_date:
            continue

        if until_date is not None and meta.date > until_date:
            continue

        filtered_files.append(meta)

    return filtered_files


def ingest_20_seconds_periodicity_weather_data(
    buffer: IO, engine: Engine, station_id: int
) -> None:
    # Read the data from a CSV file
    csv_reader = csv.DictReader(buffer, delimiter=",", strict=True)

    if csv_reader.fieldnames is None:
        raise Exception("Could not read variable names (column headers) from data file")

    # Assemble list of columns
    column_name_mapping: list[tuple[str, str]] = [
        ("Irradiance", "irradiance"),
        ("Unit_Temp_SR30", "unit_temperature_sr30"),
        ("Error_Code_SR30", "error_code_sr30"),
        ("LICOR_Irradiance", "licor_irradiance"),
        ("CUV5_Irradiance", "cuv5_irradiance"),
    ]
    columns = [
        Meteorological20SecondMeasurement.station_id.key,
        Meteorological20SecondMeasurement.timestamp.key,
    ] + [table_column_name for (_, table_column_name) in column_name_mapping]

    logger.info("Using PostgreSQL's `COPY ... FROM STDIN` command to bulk insert data")

    romania_time_zone = datetime.timezone(datetime.timedelta(hours=+2))

    with engine.connect() as conn:
        cursor = conn.connection.cursor()
        with cursor.copy(
            f"COPY {Meteorological20SecondMeasurement.__tablename__} ({', '.join(columns)}) FROM STDIN"
        ) as copy:
            for row in csv_reader:
                timestamp = datetime.datetime.fromisoformat(row["TIMESTAMP"])
                timestamp = timestamp.replace(tzinfo=romania_time_zone)

                # Extract relevant columns from this row
                measurement_values: list[str] = []
                for column_name, _ in column_name_mapping:
                    value = row[column_name]
                    measurement_values.append(value)

                copy.write_row((station_id, timestamp, *measurement_values))

        conn.connection.commit()
        cursor.close()


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

    file_counter = 0
    for entry_path in input_directory.iterdir():
        if entry_path.is_file() and entry_path.suffix == ".ghg":
            console.print(f"  Processing '{entry_path}'...")
            import_data_from_ghg_file(engine, station_id, entry_path)
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
                        import_data_from_ghg_file(engine, station_id, local_path)
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


def import_data_from_ghg_file(engine: Engine, station_id: int, ghg_file_path: Path):
    """Reads sensor measurements from a GHG file and stores them into the database."""

    # The .ghg files are actually Zip archives
    with ZipFile(ghg_file_path) as ghg_file:
        data_file_name = None

        # console.print("Zip file contents:")
        for name in ghg_file.namelist():
            # console.print(" ", name)

            if EDDY_COVARIANCE_DATA_FILE_NAME_REGEXP.match(name):
                data_file_name = name

        if data_file_name is None:
            raise Exception("Could not find data file in GHG file")

        # console.print("Data file name:", data_file_name)

        # Open the data file
        with ghg_file.open(data_file_name) as data_file:
            text_file = TextIOWrapper(data_file, encoding="ASCII")

            # Read the file header
            model = text_file.readline()
            serial_number = text_file.readline()
            instrument_name = text_file.readline()
            _file_type = text_file.readline()
            _software_version = text_file.readline()
            timestamp = text_file.readline()
            timezone = text_file.readline()

            assert model.startswith("Model:")
            model = model.replace("Model:", "").strip()
            # console.print("Model:", model)

            assert instrument_name.startswith("Instrument:")
            instrument_name = instrument_name.replace("Instrument:", "").strip()

            assert serial_number.startswith("SN:")
            serial_number = serial_number.replace("SN:", "").strip()

            with Session(engine) as session:
                sensor = (
                    session.query(Sensor)
                    .filter_by(
                        station_id=station_id,
                        name=instrument_name,
                        serial_number=serial_number,
                    )
                    .first()
                )
                if not sensor:
                    console.print(
                        f"Previously unknown sensor of type {model}, serial number {serial_number}"
                    )

                    matching_sensor_types = session.scalars(
                        select(SensorType.id).where(
                            literal(model).icontains(SensorType.name)
                        )
                    ).all()

                    if len(matching_sensor_types) != 1:
                        raise Exception(f"Ambiguous sensor model name: {model}")

                    sensor_type_id = matching_sensor_types[0]

                    sensor = Sensor(
                        name=instrument_name,
                        station_id=station_id,
                        sensor_type_id=sensor_type_id,
                        serial_number=serial_number,
                    )
                    session.add(sensor)
                    session.commit()

                sensor_id = sensor.id

            assert timezone.startswith("Timezone:")
            timezone = timezone.replace("Timezone:", "").strip()
            # console.print("Timezone:", timezone)

            assert timezone == "Etc/GMT-2"
            zone = datetime.timezone(datetime.timedelta(hours=+2), timezone)

            # Read the actual data as a tab-separated values file
            csv_reader = csv.DictReader(text_file, delimiter="\t", strict=True)
            if csv_reader.fieldnames is None:
                raise Exception(
                    "Could not read variable names (column headers) from data file"
                )

            global ghg_file_column_name_to_table_column_name_mapping
            column_name_mapping = ghg_file_column_name_to_table_column_name_mapping
            if column_name_mapping is None:
                ghg_file_column_name_to_table_column_name_mapping = (
                    load_ghg_file_column_name_to_table_column_name_mapping()
                )
                column_name_mapping = ghg_file_column_name_to_table_column_name_mapping

            with Session(engine) as session:
                console.print(
                    "    Database session created, preparing to insert rows..."
                )

                start_time = perf_counter()

                console.print(
                    "    Using PostgreSQL's `COPY FROM` command to bulk insert data"
                )

                # Assemble list of columns
                columns = [
                    EddyCovarianceMeasurement.sensor_id.key,
                    EddyCovarianceMeasurement.timestamp.key,
                    EddyCovarianceMeasurement.nanoseconds.key,
                ]
                columns += [
                    table_column_name for _, table_column_name in column_name_mapping
                ]

                cursor = session.connection().connection.cursor()
                with cursor.copy(
                    f"COPY {EddyCovarianceMeasurement.__tablename__} ({', '.join(columns)}) FROM STDIN"
                ) as copy:
                    measurements_counter = 0
                    values_counter = 0
                    for row in csv_reader:
                        timestamp = int(row["Seconds"])
                        nanoseconds = int(row["Nanoseconds"])
                        microseconds = round(nanoseconds / 1e3)

                        timestamp = datetime.datetime.fromtimestamp(timestamp, tz=zone)
                        timestamp += datetime.timedelta(microseconds=microseconds)
                        # print("Timestamp:", timestamp)

                        # Extract relevant column values from this row
                        measurement_values: list[str] = []
                        for column_name, _ in column_name_mapping:
                            value = row[column_name]
                            measurement_values.append(value)

                        copy.write_row(
                            (sensor_id, timestamp, nanoseconds, *measurement_values)
                        )

                        values_counter += len(column_name_mapping)
                        measurements_counter += 1

                session.commit()
                cursor.close()

                end_time = perf_counter()

            console.print(
                f"    Succesfully imported {measurements_counter} rows, each with {len(column_name_mapping)} variables, for a total of {values_counter} measurements"
            )

            duration = end_time - start_time
            console.print(f"    Took {duration:.2g} seconds")


def load_ghg_file_column_name_to_table_column_name_mapping() -> list[tuple[str, str]]:
    mapping_file_path = Path(__file__).parent / "ghg_file_columns_mapping.csv"

    mappings: list[tuple[str, str]] = []
    with open(mapping_file_path, newline="") as file:
        csv_reader = csv.DictReader(file, delimiter=",")
        for row in csv_reader:
            mappings.append((row["column_name"], row["table_column_name"]))

    return mappings
