import datetime
import logging
import re
from collections.abc import Generator, Iterable
from csv import DictReader
from dataclasses import dataclass
from io import BytesIO, TextIOWrapper
from pathlib import Path
from time import perf_counter
from typing import IO
from zipfile import ZipFile

from paramiko import SFTPClient, Transport
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from ..models import EddyCovarianceMeasurement, EddyCovarianceStationInfo
from ..sftp import SFTPCredentials
from .common import (
    check_file_already_ingested,
    get_or_create_local_server,
    get_or_create_server,
    record_ingested_file,
)

logger = logging.getLogger(__name__)


@dataclass
class EddyCovarianceDataFilters:
    station_codes: list[str]
    from_timestamp: datetime.datetime | None
    until_timestamp: datetime.datetime | None


def ingest_eddy_covariance_data_from_local_path(
    path: Path,
    filters: EddyCovarianceDataFilters,
    engine: Engine,
):
    if not path.exists():
        raise Exception(f"Path doesn't exist or isn't readable: '{path}'")

    server_id = get_or_create_local_server(engine)

    station_code_mapping = _EddyCovarianceStationCodeMapping(
        engine, filters.station_codes
    )

    if path.is_file():
        with Session(engine) as session:
            if check_file_already_ingested(session, server_id, path.resolve()):
                logger.warning("Skipping already ingested file %s", path)
                return

            file_name = path.name

            metadata = _extract_eddy_covariance_file_metadata(file_name)

            if not metadata:
                logger.error(
                    f"Failed to extract metadata from data file name: '{file_name}'.\nIs the path pointing to a valid processed meteo data CSV file?"
                )
                return

            if (
                filters.station_codes
                and metadata.station_code not in filters.station_codes
            ):
                logger.warning(
                    "Not ingesting data from file since its station code is '%s', which is not in the list of selected station codes",
                    metadata.station_code,
                )
                return

            if filters.from_timestamp and metadata.timestamp < filters.from_timestamp:
                logger.warning(
                    "Not ingesting data from file since its timestamp is %s, which is before the 'from time' %s",
                    metadata.timestamp,
                    filters.from_timestamp,
                )
                return

            if filters.until_timestamp and metadata.timestamp > filters.until_timestamp:
                logger.warning(
                    "Not ingesting data from file since its timestamp is %s, which is after the 'until timestamp'",
                    metadata.timestamp,
                    filters.until_timestamp,
                )
                return

            _ingest_eddy_covariance_data_from_local_file(
                path, metadata, session, station_code_mapping
            )

            record_ingested_file(session, server_id, path)

    elif path.is_dir():
        with Session(engine) as session:
            for file_path in path.glob("**"):
                if not file_path.is_file():
                    continue

                if check_file_already_ingested(session, server_id, path.resolve()):
                    logger.warning("Skipping already ingested file %s", path)
                    return

                # Try extracting metadata from this file, to check if it is a meteo data file
                file_name = file_path.name
                metadata = _extract_eddy_covariance_file_metadata(file_name)

                if not metadata:
                    logger.warning(
                        "Skipping file '%s', since its name doesn't match pattern",
                        file_path,
                    )
                    continue

                if (
                    filters.station_codes
                    and metadata.station_code not in filters.station_codes
                ):
                    logger.warning(
                        "Skipping file '%s' due to station code filter", file_path
                    )
                    continue

                if (
                    filters.from_timestamp
                    and metadata.timestamp < filters.from_timestamp
                ):
                    logger.warning(
                        "Skipping file '%s' due to 'from time' filter", file_path
                    )
                    continue

                if (
                    filters.until_timestamp
                    and metadata.timestamp > filters.until_timestamp
                ):
                    logger.warning(
                        "Skipping file '%s' due to 'until date' filter", file_path
                    )
                    continue

                _ingest_eddy_covariance_data_from_local_file(
                    file_path, metadata, session, station_code_mapping
                )

                record_ingested_file(session, server_id, path)

    else:
        raise Exception("Path is neither a regular file, nor a directory")


def _ingest_eddy_covariance_data_from_local_file(
    file_path: Path,
    metadata: _EddyCovarianceFileMetadata,
    session: Session,
    station_code_mapping: _EddyCovarianceStationCodeMapping,
) -> None:
    station_id = station_code_mapping.get_id(metadata.station_code)

    with open(file_path, "rb") as file:
        _ingest_ghg_file(file, session, station_id)


def ingest_eddy_covariance_data_from_sftp_server(
    sftp_credentials: SFTPCredentials,
    filters: EddyCovarianceDataFilters,
    engine: Engine,
    sftp_base_path: str | None,
) -> None:
    station_code_mapping = _EddyCovarianceStationCodeMapping(
        engine, filters.station_codes
    )

    server_id = get_or_create_server(engine, sftp_credentials.host)

    logger.info("Creating SSH client...")

    transport = Transport((sftp_credentials.host, sftp_credentials.port))

    logger.info(
        "Connecting to SFTP server `%s:%d`...",
        sftp_credentials.host,
        sftp_credentials.port,
    )

    transport.connect(
        None,
        username=sftp_credentials.username,
        password=sftp_credentials.password,
        pkey=sftp_credentials.private_key,
    )

    sftp = SFTPClient.from_transport(transport)

    if sftp is None:
        raise Exception("Failed to create SFTP client")

    if sftp_base_path:
        logger.info(f"Changing directory to base path '{sftp_base_path}'")
        sftp.chdir(sftp_base_path)

    entries = set(sftp.listdir())

    if filters.station_codes:
        logger.info("Filtering directories based on selected station codes...")
    else:
        logger.info("Filtering directories based on known stations...")

    station_directory_names = station_code_mapping.get_all_sftp_directory_names()

    entries = entries & station_directory_names
    if not entries:
        raise Exception(
            "No directories could be found which match the registered eddy covariance station SFTP directory names"
        )

    logger.info("Found %d directories matching filter criteria", len(entries))

    for entry in entries:
        logger.info("Starting to process directory '%s'", entry)

        logger.info("Retrieving list of files")

        # Enter the directory for this station
        sftp.chdir(entry)

        file_names = sftp.listdir()

        logger.info("Station directory '%s' contains %d files", entry, len(file_names))

        files_with_metadata: list[tuple[str, _EddyCovarianceFileMetadata]] = []
        for file_name in file_names:
            metadata = _extract_eddy_covariance_file_metadata(file_name)

            # Skip this entry if it doesn't seem to be a valid GHG data file
            if not metadata:
                continue

            files_with_metadata.append((file_name, metadata))

        files_with_metadata = list(_filter_files(files_with_metadata, filters))

        logger.info(
            "Left with %d files after applying filters", len(files_with_metadata)
        )

        if len(files_with_metadata) == 0:
            logger.warning(
                "Skipping directory since no files within it match filter criteria"
            )
            continue

        if sftp_base_path:
            base_path = f"/{sftp_base_path.removeprefix('/')}"
        else:
            base_path = "/"

        logger.info("Beginning to read/ingest files from directory '%s'", entry)

        with Session(engine) as session:
            for file_name, metadata in files_with_metadata:
                logger.info("Processing file '%s'", file_name)

                station_id = station_code_mapping.get_id(metadata.station_code)

                path = f"{base_path}/{entry}/{file_name}"
                if check_file_already_ingested(session, server_id, path):
                    logger.warning("Skipping already ingested file '%s'...", path)
                    continue

                retrieve_start_time = perf_counter()

                with sftp.file(file_name, mode="rb", bufsize=8192) as file:
                    file_contents = file.read()

                retrieve_end_time = perf_counter()
                retrieve_duration = retrieve_end_time - retrieve_start_time

                logger.info(
                    "Retrieving eddy covariance data (.ghg) file over SFTP took %.2g seconds",
                    retrieve_duration,
                )

                buffer = BytesIO(file_contents)
                _ingest_ghg_file(buffer, session, station_id)

                record_ingested_file(session, server_id, path)

        sftp.chdir("..")


class _EddyCovarianceStationCodeMapping:
    _mapping: dict[str, tuple[str, int]]

    def __init__(self, engine: Engine, selected_station_codes: list[str] = []) -> None:
        # Construct a lookup table from station codes to station metadata
        stmt = select(
            EddyCovarianceStationInfo.station_id,
            EddyCovarianceStationInfo.sftp_directory_name,
            EddyCovarianceStationInfo.code,
        )

        # If we'll filter the data files by station code,
        # only look up associations for the stations with those codes.
        if selected_station_codes:
            stmt = stmt.where(
                EddyCovarianceStationInfo.code.in_(selected_station_codes)
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

            retrieved_station_codes = (code for (_, (_, code)) in station_ids_and_codes)
            missing_stations = set(selected_station_codes) - set(
                retrieved_station_codes
            )
            logger.error(
                "Station codes for which no corresponding station could be found in the database: %s",
                " ".join(missing_stations),
            )

            raise ValueError()

        self._mapping = {
            station_code: (sftp_directory_name, station_id)
            for (station_id, sftp_directory_name, station_code) in station_ids_and_codes
        }

    def _get_metadata(self, station_code: str) -> tuple[str, int]:
        station_metadata = self._mapping.get(station_code)

        if not station_metadata:
            raise KeyError(
                f"Could not find station with code '{station_code}' in list of stations retrieved from the database"
            )

        return station_metadata

    def get_sftp_directory(self, station_code: str) -> str:
        """Determine the name of the directory on the SFTP server
        where this station's data resides, or raise an error if the station could not be found.
        """
        return self._get_metadata(station_code)[0]

    def get_all_sftp_directory_names(self) -> set[str]:
        return set(name for (name, _) in self._mapping.values())

    def get_id(self, station_code: str) -> int:
        "Retrieve the ID associated station code, or raise an error if not found."
        return self._get_metadata(station_code)[1]


@dataclass
class _EddyCovarianceFileMetadata:
    timestamp: datetime.datetime
    station_code: str


_GHG_FILE_TIMESTAMP_PATTERN = r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})T(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})"
_GHG_FILE_STATION_CODE_PATTERN = r"(?P<station_code>\d+)"

_GHG_FILE_NAME_PATTERN = re.compile(
    f"^{_GHG_FILE_TIMESTAMP_PATTERN}_smart3-{_GHG_FILE_STATION_CODE_PATTERN}\\.ghg$"
)


def _extract_eddy_covariance_file_metadata(
    file_name: str,
) -> _EddyCovarianceFileMetadata | None:
    "Extract timestamp and station code from a GHG data file name."

    match = _GHG_FILE_NAME_PATTERN.fullmatch(file_name)

    # File does not match (probably not a data file)
    if not match:
        return None

    # Extract the file's timestamp component
    year = int(match.group("year"))
    month = int(match.group("month"))
    day = int(match.group("day"))
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second"))

    file_timestamp = datetime.datetime(year, month, day, hour, minute, second)

    station_code = str(match.group("station_code"))

    return _EddyCovarianceFileMetadata(
        timestamp=file_timestamp,
        station_code=station_code,
    )


def _filter_files(
    files_with_metadata: Iterable[tuple[str, _EddyCovarianceFileMetadata]],
    filters: EddyCovarianceDataFilters,
) -> Generator[tuple[str, _EddyCovarianceFileMetadata]]:
    "Filter a list of weather station data files using the provided criteria."

    filter_by_timestamp = (
        filters.from_timestamp is not None or filters.until_timestamp is not None
    )

    # If no filter criterion is provided, return all files
    if not filters.station_codes and not filter_by_timestamp:
        yield from files_with_metadata

    if filters.station_codes:
        allowed_station_codes = set(filters.station_codes)

    for file_name, meta in files_with_metadata:
        if filters.station_codes:
            if meta.station_code not in allowed_station_codes:
                continue

        if (
            filters.from_timestamp is not None
            and meta.timestamp < filters.from_timestamp
        ):
            continue

        if (
            filters.until_timestamp is not None
            and meta.timestamp > filters.until_timestamp
        ):
            continue

        yield file_name, meta


def _ingest_ghg_file(file: IO[bytes], session: Session, station_id: int):
    """Reads sensor measurements from a GHG file and stores them into the database."""

    logger.debug("Opening GHG file as Zip archive")

    # The .ghg files are actually Zip archives
    with ZipFile(file) as ghg_file:
        data_file_name = None

        # console.print("Zip file contents:")
        for name in ghg_file.namelist():
            # console.print(" ", name)

            if EDDY_COVARIANCE_DATA_FILE_NAME_REGEXP.match(name):
                data_file_name = name

        if data_file_name is None:
            raise Exception("Could not find data file in GHG file")

        # console.print("Data file name:", data_file_name)

        logger.debug("Opening data file within GHG file")

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

            assert timezone.startswith("Timezone:")
            timezone = timezone.replace("Timezone:", "").strip()
            # console.print("Timezone:", timezone)

            # We only accept Romania's standard time or summer time
            if timezone != "Etc/GMT-2" and timezone != "Etc/GMT-3":
                raise Exception(f"Unsupported time zone: {timezone}")

            zone = datetime.timezone(datetime.timedelta(hours=+2), timezone)

            # Read the actual data as a tab-separated values file
            csv_reader = DictReader(text_file, delimiter="\t", strict=True)
            if csv_reader.fieldnames is None:
                raise Exception(
                    "Could not read variable names (column headers) from data file"
                )

            global ghg_file_column_name_to_table_column_name_mapping
            column_name_mapping = ghg_file_column_name_to_table_column_name_mapping
            if column_name_mapping is None:
                ghg_file_column_name_to_table_column_name_mapping = (
                    _load_ghg_file_column_name_to_table_column_name_mapping()
                )
                column_name_mapping = ghg_file_column_name_to_table_column_name_mapping

            start_time = perf_counter()

            logger.info("Using PostgreSQL's `COPY FROM` command to bulk insert data")

            # Assemble list of columns
            columns = [
                EddyCovarianceMeasurement.station_id.key,
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
                        (station_id, timestamp, nanoseconds, *measurement_values)
                    )

                    values_counter += len(column_name_mapping)
                    measurements_counter += 1

            cursor.close()
            session.commit()

            end_time = perf_counter()

            logger.info(
                f"Succesfully imported {measurements_counter} rows, each with {len(column_name_mapping)} variables, for a total of {values_counter} measurements"
            )

            duration = end_time - start_time
            logger.info(f"Took {duration:.2g} seconds")


EDDY_COVARIANCE_DATA_FILE_NAME_REGEXP = re.compile(r"^.*_smart3-\d+\.data$")

ghg_file_column_name_to_table_column_name_mapping = None


def _load_ghg_file_column_name_to_table_column_name_mapping() -> list[tuple[str, str]]:
    mapping_file_path = Path(__file__).parent / "ghg_file_columns_mapping.csv"

    mappings: list[tuple[str, str]] = []
    with open(mapping_file_path, newline="") as file:
        csv_reader = DictReader(file, delimiter=",")
        for row in csv_reader:
            mappings.append((row["column_name"], row["table_column_name"]))

    return mappings
