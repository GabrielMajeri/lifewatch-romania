#!/usr/bin/env python3

import csv
import datetime
from io import StringIO, TextIOWrapper
from pathlib import Path
import re
from typing import Annotated
from zipfile import ZipFile

from dotenv import load_dotenv
from lifewatch.models import Measurement, Sensor, SensorType, Station, Variable
from rich.console import Console
from sqlalchemy import Engine, create_engine, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
import typer


# Load environment variables from `.env` file (if it exists)
load_dotenv()

console = Console()

app = typer.Typer()


@app.command()
def ingest(
    station_name: str,
    input_directory: Path,
    database_url: Annotated[
        str,
        typer.Argument(
            envvar="DATABASE_URL",
        ),
    ] = "postgresql://dev:pwd@localhost:5432/lifewatch",
    use_copy: Annotated[
        bool,
        typer.Option(
            help="Use the SQL `COPY FROM` command instead of `INSERT` to insert the parsed data into the database"
        ),
    ] = True,
) -> None:
    engine = create_engine(database_url, echo=True)

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
            import_data_from_ghg_file(engine, station_id, entry_path, use_copy)
            file_counter += 1

    console.print(f"[blue]Processed {file_counter} GHG files[/blue]")


EDDY_COVARIANCE_DATA_FILE_NAME_REGEXP = re.compile(r"^.*_smart3-\d+\.data$")


def import_data_from_ghg_file(
    engine: Engine, station_id: int, ghg_file_path: Path, use_copy: bool
):
    """Reads sensor measurements from a GHG file and stores them into the database."""

    if not ghg_file_path.exists():
        raise FileNotFoundError(
            f"`.ghg` file does not exist or is not readable: '{ghg_file_path}'"
        )

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

            column_names = set(csv_reader.fieldnames)
            # console.print("Column names:", column_names)

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
                console.print("Database session created, preparing to insert rows...")

                if use_copy:
                    console.print(
                        "Using PostgreSQL's `COPY FROM` command to bulk insert data"
                    )
                    csv_output_buffer = StringIO("")
                    csv_writer = csv.writer(csv_output_buffer, delimiter="\t")
                else:
                    console.print("Inserting each row individually using `INSERT`")

                records_counter = 0
                measurements_counter = 0
                for row in csv_reader:
                    timestamp = int(row["Seconds"])
                    nanoseconds = int(row["Nanoseconds"])
                    microseconds = round(nanoseconds / 1e3)

                    timestamp = datetime.datetime.fromtimestamp(timestamp, tz=zone)
                    timestamp += datetime.timedelta(microseconds=microseconds)
                    # print("Timestamp:", timestamp)

                    if not use_copy:
                        measurements = []

                    for (
                        column_name,
                        variable_id,
                    ) in column_name_to_variable_id_mapping.items():
                        value = row[column_name]

                        if use_copy:
                            csv_writer.writerow(
                                (sensor_id, variable_id, timestamp, nanoseconds, value)
                            )
                        else:
                            measurements.append(
                                {
                                    "sensor_id": sensor_id,
                                    "variable_id": variable_id,
                                    "timestamp": timestamp,
                                    "nanoseconds": nanoseconds,
                                    "value": value,
                                }
                            )

                    measurements_counter += len(column_name_to_variable_id_mapping)
                    records_counter += 1

                    if not use_copy:
                        stmt = insert(Measurement).values(measurements)
                        session.execute(stmt)

                        if records_counter % 256 == 0:
                            session.commit()

                if use_copy:
                    cursor = session.connection().connection.cursor()

                    # Go back to the start in order to be able to read whole data
                    csv_output_buffer.seek(0)

                    measurement_table_columns = tuple(
                        column.key
                        for column in (
                            Measurement.sensor_id,
                            Measurement.variable_id,
                            Measurement.timestamp,
                            Measurement.nanoseconds,
                            Measurement.value,
                        )
                    )
                    cursor.copy_from(
                        csv_output_buffer,
                        Measurement.__tablename__,
                        sep="\t",
                        columns=measurement_table_columns,
                    )

                    session.commit()
                    cursor.close()

            print(
                f"Succesfully imported {records_counter} rows, each with {len(column_name_to_variable_id_mapping.keys())} variables, for a total of {measurements_counter} measurements"
            )


if __name__ == "__main__":
    app()
