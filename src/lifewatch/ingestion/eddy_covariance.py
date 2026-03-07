import logging
from pathlib import Path
from zipfile import ZipFile

from sqlalchemy import Engine


logger = logging.getLogger(__name__)


def import_eddy_covariance_data_from_ghg_file(
    engine: Engine, station_id: int, ghg_file_path: Path
):
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
