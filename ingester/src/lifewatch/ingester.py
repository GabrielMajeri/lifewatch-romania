#!/usr/bin/env python3

import base64
import csv
import datetime
from io import TextIOWrapper
from pathlib import Path
import re
from stat import S_ISREG
from tempfile import gettempdir
from time import perf_counter
from typing import Annotated
from zipfile import ZipFile

from dotenv import load_dotenv
from lifewatch.models import EddyCovarianceMeasurement, Sensor, SensorType, Station
from paramiko import Ed25519Key, PKey, SFTPClient, SSHClient
from rich.console import Console
from sqlalchemy import Engine, create_engine, literal, select
from sqlalchemy.orm import Session
import typer


# Load environment variables from `.env` file (if it exists)
load_dotenv()

console = Console()

app = typer.Typer()


@app.command(help="Import files from a local directory.")
def local(
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
def remote(
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
    mapping_file_path = Path(__file__).parents[2] / "ghg_file_columns_mapping.csv"

    mappings: list[tuple[str, str]] = []
    with open(mapping_file_path, newline="") as file:
        csv_reader = csv.DictReader(file, delimiter=",")
        for row in csv_reader:
            mappings.append((row["column_name"], row["table_column_name"]))

    return mappings


if __name__ == "__main__":
    app()
