import base64
import csv
import datetime
from io import TextIOWrapper
from pathlib import Path
import re
from tempfile import gettempdir
from zipfile import ZipFile

from paramiko import Ed25519Key, SFTPClient, SSHClient, PKey
from sqlalchemy import literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from tqdm.contrib import tenumerate

from lifewatch.db import engine
from lifewatch.models import Measurement, Sensor, SensorType, Station, Variable


DATA_COLLECTION_SERVER_HOSTNAME: str = "colectare.lifewatch.unibuc.ro"


class DataCollectorClient:
    "Client for connecting to the data collection machine and reading data files from it."

    ssh_client: SSHClient
    sftp_client: SFTPClient

    def __init__(self) -> None:
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
        private_key_path = "id_database"
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


def import_data_from_ghg_file(station_id: int, ghg_file_path: Path):
    """Reads sensor measurements from a GHG file and stores them into the database."""

    if not ghg_file_path.exists():
        raise FileNotFoundError(
            f".ghg file does not exist or is not readable: '{ghg_file_path}'"
        )

    # The .ghg files are actually Zip archives
    with ZipFile(ghg_file_path) as ghg_file:
        data_file_name = None

        # print("Zip file contents:")
        for name in ghg_file.namelist():
            # print(" ", name)

            if EDDY_COVARIANCE_DATA_FILE_NAME_REGEXP.match(name):
                data_file_name = name

        if data_file_name is None:
            raise Exception("Could not find data file in GHG file")

        print("Interesting data file:", data_file_name)

        # Open the data file
        with ghg_file.open(data_file_name) as data_file:
            text_file = TextIOWrapper(data_file, encoding="ASCII")

            # Read the file header
            model = text_file.readline()
            serial_number = text_file.readline()
            instrument_name = text_file.readline()
            file_type = text_file.readline()
            software_version = text_file.readline()
            timestamp = text_file.readline()
            timezone = text_file.readline()

            assert model.startswith("Model:")
            model = model.replace("Model:", "").strip()
            print("Model:", model)

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
                    print(
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
            print("Timezone:", timezone)

            assert timezone == "Etc/GMT-2"
            zone = datetime.timezone(datetime.timedelta(hours=+2), timezone)

            # Read the actual data as a tab-separated values file
            csv_reader = csv.DictReader(text_file, delimiter="\t", strict=True)
            if csv_reader.fieldnames is None:
                raise Exception(
                    "Could not read variable names (column headers) from data file"
                )

            column_names = set(csv_reader.fieldnames)
            # print("Column names:", column_names)

            column_name_to_variable_name_mapping = {
                "CO2 (mmol/m^3)": "CO2 concentration",
                "CO2 (umol/mol)": "CO2 molar fraction",
                "CO2 Absorptance": "CO2 absorptance",
            }

            column_name_to_variable_id_mapping = {}
            with Session(engine) as session:
                for (
                    column_name,
                    variable_name,
                ) in column_name_to_variable_name_mapping.items():
                    variable_id = session.scalar(
                        select(Variable.id).where(Variable.name == variable_name)
                    )

                    if variable_id is None:
                        raise Exception(
                            f"Could not find variable with name '{variable_name}' in the database"
                        )

                    column_name_to_variable_id_mapping[column_name] = variable_id

            assert column_name_to_variable_id_mapping.keys() <= column_names, (
                f"Some required columns are missing from the file: {column_name_to_variable_id_mapping.keys() - column_names}"
            )

            with Session(engine) as session:
                print("Database session opened, beginning to insert rows...")

                counter = 0
                for index, row in tenumerate(csv_reader):
                    # print(f"Row #{index + 1}:")

                    timestamp = int(row["Seconds"])

                    nanoseconds = int(row["Nanoseconds"])
                    microseconds = round(nanoseconds / 1e3)

                    timestamp = datetime.datetime.fromtimestamp(timestamp, tz=zone)
                    timestamp += datetime.timedelta(microseconds=microseconds)
                    # print("Timestamp:", timestamp)

                    for (
                        column_name,
                        variable_id,
                    ) in column_name_to_variable_id_mapping.items():
                        value = row[column_name]
                        stmt = (
                            insert(Measurement)
                            .values(
                                sensor_id=sensor_id,
                                variable_id=variable_id,
                                timestamp=timestamp,
                                value=value,
                            )
                            .on_conflict_do_nothing()
                        )
                        session.execute(stmt)

                    counter += 1
                    if counter % 256 == 0:
                        session.commit()

            print(
                f"Succesfully imported {counter} rows, each with {len(column_name_to_variable_id_mapping.keys())} variables"
            )


if __name__ == "__main__":
    with DataCollectorClient() as client:
        # Download the remote data file to a local temporary file
        client.sftp_client.chdir("icos")

        station_name = "Brașov"
        with Session(engine) as session:
            station_id = session.scalar(
                select(Station.id).where(Station.name == station_name)
            )
            if station_id is None:
                raise Exception(f"Could not find station '{station_name}'")

        remote_path = "bv/2025-08-05T150000_smart3-01014.ghg"

        # Generate a local temporary file path
        temp_dir = gettempdir()
        local_dir = Path(temp_dir) / "eddy_covariance"
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / "2025-08-05T150000_smart3-01014.ghg"

        client.sftp_client.get(remote_path, local_path)

        try:
            import_data_from_ghg_file(station_id, local_path)

        finally:
            local_path.unlink(missing_ok=True)
            local_dir.rmdir()
