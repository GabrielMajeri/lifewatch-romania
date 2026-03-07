from collections.abc import Generator, Iterable
from csv import DictReader
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from io import BytesIO, StringIO
import logging
from pathlib import Path
import re
from time import perf_counter
from typing import IO, cast

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from ..ftp import FTPCredentials, Fixed_FTP_TLS
from ..models import Meteorological20SecondMeasurement, MeteorologicalStationInfo


logger = logging.getLogger(__name__)


class MeteorologicalVariablePeriodicity(StrEnum):
    EVERY_20_SECONDS = "20s"
    EVERY_30_SECONDS = "30s"
    EVERY_60_SECONDS = "60s"


@dataclass
class MeteorologicalDataFilters:
    periodicities: list[MeteorologicalVariablePeriodicity]
    station_codes: list[str]
    from_date: date | None
    until_date: date | None


def ingest_meteo_data_from_local_path(
    path: Path,
    filters: MeteorologicalDataFilters,
    engine: Engine,
) -> None:
    if not path.exists():
        raise Exception(f"Path doesn't exist or isn't readable: '{path}'")

    station_code_mapping = StationCodeMapping(engine, filters.station_codes)

    if path.is_file():
        file_name = path.name

        metadata = extract_meteo_file_metadata(file_name)

        if not metadata:
            logger.error(
                f"Failed to extract metadata from data file name : '{file_name}'.\nIs the path pointing to a valid processed meteo data CSV file?"
            )
            return

        if metadata.period not in filters.periodicities:
            logger.warning(
                "Not ingesting data from file since its periodicty is '%s', which is not in the list of selected periodicities",
                metadata.period,
            )
            return

        if filters.station_codes and metadata.station_code not in filters.station_codes:
            logger.warning(
                "Not ingesting data from file since its station code is '%s', which is not in the list of selected station codes",
                metadata.station_code,
            )
            return

        if filters.from_date and metadata.date < filters.from_date:
            logger.warning(
                "Not ingesting data from file since its date is %s, which is before the 'from date' %s",
                metadata.date,
                filters.from_date,
            )
            return

        if filters.until_date and metadata.date > filters.until_date:
            logger.warning(
                "Not ingesting data from file since its date is %s, which is after the 'until date'",
                metadata.date,
                filters.until_date,
            )
            return

        _ingest_meteo_data_from_local_file(path, metadata, engine, station_code_mapping)

    elif path.is_dir():
        for file_path in path.glob("**"):
            if not file_path.is_file():
                continue

            # Try extracting metadata from this file, to check if it is a meteo data file
            file_name = file_path.name
            metadata = extract_meteo_file_metadata(file_name)

            if not metadata:
                logger.warning(
                    "Skipping file '%s', since its name doesn't match pattern",
                    file_path,
                )
                continue

            if metadata.period not in filters.periodicities:
                logger.info("Skipping file '%s' due to periodicity filter", file_path)
                continue

            if (
                filters.station_codes
                and metadata.station_code not in filters.station_codes
            ):
                logger.warning(
                    "Skipping file '%s' due to station code filter", file_path
                )
                continue

            if filters.from_date and metadata.date < filters.from_date:
                logger.warning(
                    "Skipping file '%s' due to 'from date' filter", file_path
                )
                continue

            if filters.until_date and metadata.date > filters.until_date:
                logger.warning(
                    "Skipping file '%s' due to 'until date' filter", file_path
                )
                continue

            _ingest_meteo_data_from_local_file(
                file_path, metadata, engine, station_code_mapping
            )

    else:
        raise Exception("Path is neither a regular file, nor a directory")


def _ingest_meteo_data_from_local_file(
    file_path: Path,
    metadata: MeteorologicalFileMetadata,
    engine: Engine,
    station_code_mapping: StationCodeMapping,
) -> None:
    station_id = station_code_mapping.get(metadata.station_code)

    if metadata.period == MeteorologicalVariablePeriodicity.EVERY_20_SECONDS:
        with open(file_path, "r") as file:
            ingest_20_seconds_periodicity_weather_data(file, engine, station_id)

    else:
        raise NotImplementedError(
            f"Ingestion not yet implemented for meteorological data with '{metadata.period}' periodicity"
        )


def ingest_meteo_data_from_ftp_server(
    ftp_credentials: FTPCredentials,
    filters: MeteorologicalDataFilters,
    engine: Engine,
) -> None:
    station_code_mapping = StationCodeMapping(engine, filters.station_codes)

    logger.info(
        "Connecting to FTP server `%s:%d`...",
        ftp_credentials.host,
        ftp_credentials.port,
    )

    with Fixed_FTP_TLS() as ftp:
        response = ftp.connect(ftp_credentials.host, ftp_credentials.port, timeout=3)
        logger.debug("FTP connect response: %s", response)

        response = ftp.auth()
        logger.debug("FTP auth response: %s", response)

        response = ftp.login(ftp_credentials.user, ftp_credentials.password)
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
            ", ".join(p.value for p in filters.periodicities),
        )

        if MeteorologicalVariablePeriodicity.EVERY_20_SECONDS in filters.periodicities:
            if "20" not in processed_data_dir_entries:
                raise RuntimeError(
                    "Directory for variables with periodicity of 20 seconds not found"
                )

            response = ftp.cwd("20")
            logger.debug("FTP change current directory response: %s", response)

            data_files_20s_periodicity = ftp.nlst()
            # logger.debug("20 seconds directory listing: %s", data_files_20s_periodicity)

            # Extract station identifiers and file dates
            files_metadata = [
                extract_meteo_file_metadata(file_name)
                for file_name in data_files_20s_periodicity
            ]

            files_and_metadata = zip(data_files_20s_periodicity, files_metadata)

            # Skip over files whose names don't match meteo data file pattern
            # (hence we cannot determine metadata for them)
            files_with_metadata = filter(lambda p: p[1] is not None, files_and_metadata)

            files_with_metadata = cast(
                Iterable[tuple[str, MeteorologicalFileMetadata]], files_with_metadata
            )

            # Filter files based on user-provided criteria
            files_with_metadata = list(_filter_files(files_with_metadata, filters))

            if not files_with_metadata:
                raise ValueError(
                    "No data files were found matching the provided filters"
                )

            logger.info(
                "Found %d files matching provided filters",
                len(files_with_metadata),
            )

            station_ids = [
                station_code_mapping.get(metadata[1].station_code)
                for metadata in files_with_metadata
            ]

            logger.info("Beginning to read/ingest files")

            for (file_name, _metadata), station_id in zip(
                files_with_metadata, station_ids
            ):
                logger.info("Processing file '%s'", file_name)

                retrieve_start_time = perf_counter()

                buffer = BytesIO()
                response = ftp.retrbinary(f"RETR {file_name}", buffer.write)
                logger.debug("FTP retrieve file response: %s", response)

                retrieve_end_time = perf_counter()
                retrieve_duration = retrieve_end_time - retrieve_start_time
                logger.info(
                    "Retrieving file binary contents took %.3g seconds",
                    retrieve_duration,
                )

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


class StationCodeMapping:
    _mapping: dict[str, int]

    def __init__(self, engine: Engine, selected_station_codes: list[str] = []) -> None:
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

            raise ValueError()

        self._mapping = {
            station_code: station_id
            for (station_id, station_code) in station_ids_and_codes
        }

    def get(self, station_code: str) -> int:
        "Retrieve the ID associated station code, or raise an error if not found."

        station_id = self._mapping.get(station_code)

        if not station_id:
            raise KeyError(
                f"Could not find station with code '{station_code}' in list of stations retrieved from the database"
            )

        return station_id


@dataclass
class MeteorologicalFileMetadata:
    station_code: str
    date: date
    period: MeteorologicalVariablePeriodicity


METEO_FILE_STATION_CODE_PATTERN = r"(?P<station_code>\w{2}-\w{3})"
METEO_FILE_DATE_PATTERN = r"(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})"
METEO_FILE_PERIOD_PATTERN = r"(?P<period>F\d{2})"

METEO_FILE_PATTERN = re.compile(
    f"^{METEO_FILE_STATION_CODE_PATTERN}_BM_{METEO_FILE_DATE_PATTERN}_L\\d{{2}}_{METEO_FILE_PERIOD_PATTERN}\\.csv$"
)


def extract_meteo_file_metadata(file_name: str) -> MeteorologicalFileMetadata | None:
    "Extract station code, record date and sampling period from a (processed) meteorological data file name."

    match = METEO_FILE_PATTERN.fullmatch(file_name)

    # File does not match (probably not a data file)
    if not match:
        return None

    station_code = str(match.group("station_code"))

    # Extract the file's date component
    year = int(match.group("year"))
    month = int(match.group("month"))
    day = int(match.group("day"))
    file_date = date(year, month, day)

    # Extract the variable measurement periodicity
    period_code = match.group("period")
    if period_code == "F01":
        period = MeteorologicalVariablePeriodicity.EVERY_20_SECONDS
    elif period_code == "F02":
        period = MeteorologicalVariablePeriodicity.EVERY_30_SECONDS
    elif period_code == "F03":
        period = MeteorologicalVariablePeriodicity.EVERY_60_SECONDS
    else:
        raise ValueError(f"Unknown measurement periodicity code: '{period_code}'")

    return MeteorologicalFileMetadata(
        station_code=station_code, date=file_date, period=period
    )


def _filter_files(
    files_with_metadata: Iterable[tuple[str, MeteorologicalFileMetadata]],
    filters: MeteorologicalDataFilters,
) -> Generator[tuple[str, MeteorologicalFileMetadata]]:
    "Filter a list of weather station data files using the provided criteria."

    filter_by_date = filters.from_date is not None or filters.until_date is not None

    # If no filter criterion is provided, return all files
    if not filters.station_codes and not filter_by_date:
        yield from files_with_metadata

    if filters.station_codes:
        allowed_station_codes = set(filters.station_codes)

    for file_name, meta in files_with_metadata:
        if filters.station_codes:
            if meta.station_code not in allowed_station_codes:
                continue

        if filters.from_date is not None and meta.date < filters.from_date:
            continue

        if filters.until_date is not None and meta.date > filters.until_date:
            continue

        yield file_name, meta


def ingest_20_seconds_periodicity_weather_data(
    buffer: IO, engine: Engine, station_id: int
) -> None:
    # Read the data from a CSV file
    csv_reader = DictReader(buffer, delimiter=",", strict=True)

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

    required_columns = set(
        ["TIMESTAMP"] + [csv_column_name for csv_column_name, _ in column_name_mapping]
    )
    csv_columns = set(csv_reader.fieldnames)
    if not required_columns <= csv_columns:
        difference = required_columns - csv_columns
        raise Exception(
            f"CSV is missing some required columns: {', '.join(difference)}"
        )

    columns = [
        Meteorological20SecondMeasurement.station_id.key,
        Meteorological20SecondMeasurement.timestamp.key,
    ] + [table_column_name for (_, table_column_name) in column_name_mapping]

    logger.info("Using PostgreSQL's `COPY ... FROM STDIN` command to bulk insert data")

    romania_time_zone = timezone(timedelta(hours=+2))

    copy_start_time = perf_counter()
    num_rows = 0

    with engine.connect() as conn:
        cursor = conn.connection.cursor()
        with cursor.copy(
            f"COPY {Meteorological20SecondMeasurement.__tablename__} ({', '.join(columns)}) FROM STDIN"
        ) as copy:
            for row in csv_reader:
                timestamp = datetime.fromisoformat(row["TIMESTAMP"])
                timestamp = timestamp.replace(tzinfo=romania_time_zone)

                # Extract relevant columns from this row
                measurement_values: list[str] = []
                for column_name, _ in column_name_mapping:
                    value = row[column_name]

                    # For some strange reason, in some files,
                    # this column is a float (usually 0)
                    if column_name == "Error_Code_SR30":
                        value = str(int(float(value)))

                    measurement_values.append(value)

                copy.write_row((station_id, timestamp, *measurement_values))
                num_rows += 1

        conn.connection.commit()
        cursor.close()

    copy_end_time = perf_counter()
    copy_duration = copy_end_time - copy_start_time
    logger.info(
        "Took %.3g seconds to copy %d rows of data into the database",
        copy_duration,
        num_rows,
    )
