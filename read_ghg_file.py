import csv
import datetime
from io import TextIOWrapper
from pathlib import Path
import time
from zipfile import ZipFile

from tqdm.contrib import tenumerate

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from lifewatch.db.engine import engine
from lifewatch.db.models import Sensor, Variable, Measurement


def get_or_create_variable(
    session: Session, label: str, name: str, unit: str
) -> Variable:
    variable = session.query(Variable).filter_by(label=label).first()

    if variable:
        assert variable.name == name, "Existing variable has different name"
        assert variable.unit == unit, "Existing variable has different measuring unit"
    else:
        variable = Variable(label=label, name=name, unit=unit)
        session.add(variable)
        session.commit()

    return variable


# Ensure interesting variables have been created beforehand
variable_ids_mapping: dict[str, int] = {}
with Session(engine) as session:
    variable_ids_mapping["co2_concentration"] = get_or_create_variable(
        session,
        "CO2_CONC",
        "CO2 Concentration Density",
        "mmolCO2 m^(-3)",
    ).id
    variable_ids_mapping["co2_molar_fraction"] = get_or_create_variable(
        session,
        "CO2",
        "CO2 Molar Fraction",
        "umolCO2 mol^(-1)",
    ).id
    variable_ids_mapping["co2_absorptance"] = get_or_create_variable(
        session,
        "CO2_ABS",
        "CO2 Absorptance",
        "adimensional",
    ).id

# Construct a mapping for the LICOR analyzer
column_name_to_variable_key_mapping = {
    "CO2 (mmol/m^3)": "co2_concentration",
    "CO2 (umol/mol)": "co2_molar_fraction",
    "CO2 Absorptance": "co2_absorptance",
}

column_name_to_variable_id_mapping = {
    key: variable_ids_mapping[value]
    for key, value in column_name_to_variable_key_mapping.items()
}

# Open the sample input file
input_file_path = Path(__file__).parent / "2024-05-12T123000_smart3-01015.ghg"
assert input_file_path.exists(), "Input file not found"

# The .ghg files are actually Zip archives
with ZipFile(input_file_path) as zipfile:
    data_file_name = None

    print("Zip file contents:")
    for name in zipfile.namelist():
        print(" ", name)

        if name.endswith("_smart3-01015.data"):
            data_file_name = name

    assert data_file_name is not None, "Data file not found in archive"

    # Open the data file
    with zipfile.open(data_file_name) as data_file:
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

        with Session(engine) as session:
            sensor = session.query(Sensor).filter_by(name=model).first()
            if not sensor:
                sensor = Sensor(name=model)
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

        column_names = set(csv_reader.fieldnames)
        # print("Column names:", column_names)
        assert column_name_to_variable_id_mapping.keys() <= column_names, (
            f"Some required columns are missing from the file: {column_name_to_variable_id_mapping.keys() - column_names}"
        )

        with Session(engine) as session:
            print("Database session opened, beginning to insert rows...")

            counter = 0
            for index, row in tenumerate(csv_reader):
                # print(f"Row #{index + 1}:")

                timestamp = int(row["Seconds"])
                time_tuple = time.gmtime(timestamp)

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
            f"Succesfully imported {counter} rows, each with {len(variable_ids_mapping.keys())} variables"
        )
