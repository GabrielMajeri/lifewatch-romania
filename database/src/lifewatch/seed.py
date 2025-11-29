import csv
import json
from pathlib import Path
import traceback
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .db import Base, engine
from .models import SensorType, Station, Variable


def create_tables():
    print("Checking if database tables exist and creating them as needed")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    print("Database tables have been created")


def read_csv_seed_file(name: str) -> list[dict[str, Any]]:
    print(f"Loading seed data file '{name}'")

    seed_files_directory = Path(__file__).parents[2] / "seed_data"
    seed_file_path = seed_files_directory / name

    with open(seed_file_path, newline="") as file:
        reader = csv.DictReader(file)

        return list(reader)


def convert_json_column_to_dict(
    data: list[dict[str, Any]], column_name: str
) -> list[dict[str, Any]]:
    return [
        {
            name: json.loads(value) if name == column_name else value
            for name, value in row.items()
        }
        for row in data
    ]


def seed_initial_data():
    print("Ensuring seed data exists in database")

    with Session(engine) as session:
        # Import variables
        variables_data = read_csv_seed_file("variables.csv")
        variables_data = convert_json_column_to_dict(variables_data, "extra")
        _ = session.execute(
            insert(Variable).on_conflict_do_nothing(index_elements=["name"]),
            variables_data,
        )

        # Import stations
        stations_data = read_csv_seed_file("stations.csv")
        stations_data = convert_json_column_to_dict(stations_data, "extra")
        _ = session.execute(
            insert(Station).on_conflict_do_nothing(index_elements=["name"]),
            stations_data,
        )

        # Import sensor types
        sensor_types_data = read_csv_seed_file("sensor_types.csv")
        _ = session.execute(
            insert(SensorType)
            .on_conflict_do_nothing(index_elements=["name"])
            .returning(SensorType),
            sensor_types_data,
        )

        session.commit()

    print("Seed data has been inserted")


def main() -> int:
    print("Lifewatch UB database seed tool is starting")

    try:
        create_tables()
        seed_initial_data()
    except Exception:
        print(traceback.format_exc())
        return 1

    print("Seed code ran succesfully")

    return 0
