import csv
import datetime
from io import TextIOWrapper
from pathlib import Path
import time
from zipfile import ZipFile


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
        print(timestamp)

        timezone = text_file.readline()

        assert timezone.startswith("Timezone:")
        timezone = timezone.replace("Timezone:", "").strip()
        print("Timezone:", timezone)

        assert timezone == "Etc/GMT-2"
        zone = datetime.timezone(datetime.timedelta(hours=+2), timezone)

        # Read the actual data as a tab-separated values file
        csv_reader = csv.DictReader(text_file, delimiter="\t", strict=True)

        print("Column names:", csv_reader.fieldnames)

        for index, row in enumerate(csv_reader):
            print(f"Row #{index + 1}:")

            timestamp = int(row["Seconds"])
            time_tuple = time.gmtime(timestamp)

            nanoseconds = int(row["Nanoseconds"])
            microseconds = round(nanoseconds / 1e3)

            timestamp = datetime.datetime.fromtimestamp(timestamp, tz=zone)
            timestamp += datetime.timedelta(microseconds=microseconds)
            print("Timestamp:", timestamp)

            print(row)

            # Only print a portion of the data, for development purposes
            if index == 3:
                break
