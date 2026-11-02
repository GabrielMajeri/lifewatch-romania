import csv
import datetime
import logging
import math
import re
from collections.abc import Generator, Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from io import BytesIO, TextIOWrapper
from pathlib import Path
from time import perf_counter
from typing import IO, TextIO
from zipfile import ZipFile

from paramiko import SFTPClient, Transport
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from ..models import (
    EddyCovarianceCh4Measurement,
    EddyCovarianceMeasurement,
    EddyCovarianceStationInfo,
)
from ..sftp import SFTPCredentials
from .common import (
    IngestionResult,
    canonicalize_path,
    get_already_ingested_paths,
    get_or_create_local_server,
    get_or_create_server,
    get_worker_engine,
    record_ingested_file,
)

logger = logging.getLogger(__name__)


@dataclass
class EddyCovarianceDataFilters:
    station_codes: list[str]
    from_timestamp: datetime.datetime | None
    until_timestamp: datetime.datetime | None


EC_BASE_COLUMNS: list[tuple[str, str, type]] = [
    ("Sequence Number", "sequence_number", int),
    ("Diagnostic Value", "diagnostic_value", int),
    ("Diagnostic Value 2", "diagnostic_value_2", int),
    ("DS Diagnostic Value", "ds_diagnostic_value", int),
    ("CO2 Absorptance", "co2_absorptance", float),
    ("H2O Absorptance", "h2o_absorptance", float),
    ("CO2 (mmol/m^3)", "co2_concentration", float),
    ("CO2 (mg/m^3)", "co2_mass_density", float),
    ("H2O (mmol/m^3)", "h2o_concentration", float),
    ("H2O (g/m^3)", "h2o_mass_density", float),
    ("Temperature (C)", "temperature", float),
    ("Pressure (kPa)", "pressure", float),
    ("Cooler Voltage (V)", "cooler_voltage", float),
    ("Chopper Cooler Voltage (V)", "chopper_cooler_voltage", float),
    ("Vin SmartFlux (V)", "vin_smart_flux", float),
    ("CO2 (umol/mol)", "co2_molar_fraction", float),
    ("H2O (mmol/mol)", "h2o_molar_fraction", float),
    ("Dew Point (C)", "dew_point", float),
    ("CO2 Signal Strength", "co2_signal_strength", float),
    ("H2O Sample", "h2o_raw_power_in_sample_wavelength", float),
    ("H2O Reference", "h2o_raw_power_in_reference_wavelength", float),
    ("CO2 Sample", "co2_raw_power_in_sample_wavelength", float),
    ("CO2 Reference", "co2_raw_power_in_reference_wavelength", float),
    ("Vin DSI (V)", "vin_dsi", float),
    ("U (m/s)", "wind_u", float),
    ("V (m/s)", "wind_v", float),
    ("W (m/s)", "wind_w", float),
    ("T (C)", "sonic_temperature", float),
    ("Anemometer Diagnostics", "anemometer_diagnostics", int),
    ("CHK", "chk", int),
]

CH4_COLUMNS: list[tuple[str, str, type]] = [
    ("CH4 (umol/mol)", "ch4_molar_fraction", float),
    ("CH4 (mmol/m^3)", "ch4_molar_density", float),
    ("CH4 Temperature", "ch4_temperature", float),
    ("CH4 Pressure", "ch4_pressure", float),
    ("CH4 Signal Strength", "ch4_signal_strength", float),
    ("CH4 Auxiliary Input 1", "ch4_auxiliary_input_1", float),
    ("CH4 Auxiliary Input 2", "ch4_auxiliary_input_2", float),
    ("CH4 Auxiliary Input 3", "ch4_auxiliary_input_3", float),
    ("CH4 Auxiliary Input 4", "ch4_auxiliary_input_4", float),
    ("CH4 Thermocouple Input 1", "ch4_thermocouple_input_1", float),
    ("CH4 Thermocouple Input 2", "ch4_thermocouple_input_2", float),
    ("CH4 Thermocouple Input 3", "ch4_thermocouple_input_3", float),
    ("CH4 Diagnostic Value", "ch4_diagnostic_value", int),
    ("CH4 Drop Rate (%)", "ch4_drop_rate", float),
]

MISSING_VALUES = frozenset(
    {"-9999", "-9999.0", "nan", "-nan", "null", "none", "", "NAN", "-NAN", "NULL", "NONE"}
)

NON_NULL_BASE_COLS = frozenset(
    {
        "Sequence Number",
        "Diagnostic Value",
        "Diagnostic Value 2",
        "DS Diagnostic Value",
        "Anemometer Diagnostics",
        "CHK",
    }
)


def _parse_val(
    val: str | None, col_type: type, allow_null: bool = True
) -> int | float | None:
    if val is None:
        return None
    val_str = val.strip()
    if not val_str or val_str in MISSING_VALUES:
        return None if allow_null else 0
    try:
        if col_type is int:
            return int(float(val_str))
        else:
            f = float(val_str)
            if math.isnan(f) or math.isinf(f):
                return None
            return f
    except (ValueError, TypeError):
        return None


@dataclass
class _EddyCovarianceFileMetadata:
    timestamp: datetime.datetime
    station_code: str


_GHG_FILE_TIMESTAMP_PATTERN = r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})T(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})"
_GHG_FILE_STATION_CODE_PATTERN = r"(?P<station_code>\d+)"

_GHG_FILE_NAME_PATTERN = re.compile(
    f"^{_GHG_FILE_TIMESTAMP_PATTERN}_smart3-{_GHG_FILE_STATION_CODE_PATTERN}(?:\\.ghg|\\.data|\\.ghg\\.zip)$"
)

EDDY_COVARIANCE_DATA_FILE_NAME_REGEXP = re.compile(r"^.*_smart3-\d+\.data$")


def _extract_eddy_covariance_file_metadata(
    file_name: str,
) -> _EddyCovarianceFileMetadata | None:
    "Extract timestamp and station code from a GHG data file name."
    match = _GHG_FILE_NAME_PATTERN.fullmatch(file_name)
    if not match:
        return None

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

    if not filters.station_codes and not filter_by_timestamp:
        yield from files_with_metadata
        return

    allowed_station_codes = set(filters.station_codes) if filters.station_codes else None

    for file_name, meta in files_with_metadata:
        if allowed_station_codes and meta.station_code not in allowed_station_codes:
            continue

        if filters.from_timestamp is not None and meta.timestamp < filters.from_timestamp:
            continue

        if filters.until_timestamp is not None and meta.timestamp > filters.until_timestamp:
            continue

        yield file_name, meta


class _EddyCovarianceStationCodeMapping:
    _mapping: dict[str, tuple[str, int]]

    def __init__(
        self, engine: Engine, selected_station_codes: list[str] | None = None
    ) -> None:
        selected_codes = selected_station_codes or []
        stmt = select(
            EddyCovarianceStationInfo.station_id,
            EddyCovarianceStationInfo.sftp_directory_name,
            EddyCovarianceStationInfo.code,
        )

        if selected_codes:
            stmt = stmt.where(
                EddyCovarianceStationInfo.code.in_(selected_codes)
            )

        with Session(engine) as session:
            result = session.execute(stmt)
            station_ids_and_codes = result.all()

        if selected_codes and len(station_ids_and_codes) != len(selected_codes):
            logger.error(
                "Could not find stations corresponding to some of the provided station codes"
            )
            retrieved_station_codes = {code for (_, (_, code)) in station_ids_and_codes}
            missing_stations = set(selected_codes) - retrieved_station_codes
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
        return self._get_metadata(station_code)[0]

    def get_all_sftp_directory_names(self) -> set[str]:
        return {name for (name, _) in self._mapping.values()}

    def get_id(self, station_code: str) -> int:
        return self._get_metadata(station_code)[1]


def _ingest_eddy_covariance_data_stream(
    text_file: TextIO, session: Session, station_id: int
) -> tuple[int, int]:
    # Read the file header
    model = text_file.readline()
    serial_number = text_file.readline()
    instrument_name = text_file.readline()
    _file_type = text_file.readline()
    _software_version = text_file.readline()
    _timestamp_line = text_file.readline()
    timezone_line = text_file.readline()

    assert model.startswith("Model:")
    assert instrument_name.startswith("Instrument:")
    assert serial_number.startswith("SN:")
    assert timezone_line.startswith("Timezone:")

    timezone_str = timezone_line.replace("Timezone:", "").strip()

    if timezone_str == "Etc/GMT-2":
        offset = datetime.timedelta(hours=+2)
    elif timezone_str == "Etc/GMT-3":
        offset = datetime.timedelta(hours=+3)
    else:
        raise ValueError(f"Unsupported time zone: {timezone_str}")

    zone = datetime.timezone(offset, timezone_str)

    # Use fast index-mapped csv reader
    csv_reader = csv.reader(text_file, delimiter="\t")
    fieldnames = next(csv_reader, None)
    if not fieldnames:
        raise ValueError("Could not read variable names (column headers) from data file")

    name_to_idx = {name: i for i, name in enumerate(fieldnames)}
    if "Seconds" not in name_to_idx or "Nanoseconds" not in name_to_idx:
        raise ValueError("Missing required Seconds/Nanoseconds columns in data file")

    sec_idx = name_to_idx["Seconds"]
    nano_idx = name_to_idx["Nanoseconds"]

    # Assemble column extractors for base EC measurements
    ec_extractors = [
        (name_to_idx[file_col], col_type, file_col not in NON_NULL_BASE_COLS)
        for file_col, _, col_type in EC_BASE_COLUMNS
        if file_col in name_to_idx
    ]

    base_columns = [
        EddyCovarianceMeasurement.station_id.key,
        EddyCovarianceMeasurement.timestamp.key,
        EddyCovarianceMeasurement.nanoseconds.key,
    ] + [tbl_col for file_col, tbl_col, _ in EC_BASE_COLUMNS if file_col in name_to_idx]

    has_ch4 = "CH4 Seconds" in name_to_idx
    ch4_sec_idx = name_to_idx.get("CH4 Seconds")
    ch4_nano_idx = name_to_idx.get("CH4 Nanoseconds")
    ch4_extractors = [
        (name_to_idx[file_col], col_type, file_col != "CH4 Diagnostic Value")
        for file_col, _, col_type in CH4_COLUMNS
        if file_col in name_to_idx
    ] if has_ch4 else []

    ch4_rows: list[tuple] = []
    ec_measurements_counter = 0

    cursor = session.connection().connection.cursor()
    with cursor.copy(
        f"COPY {EddyCovarianceMeasurement.__tablename__} ({', '.join(base_columns)}) FROM STDIN"
    ) as copy:
        for row in csv_reader:
            if not row:
                continue
            sec = int(row[sec_idx])
            nanoseconds = int(row[nano_idx])
            microseconds = round(nanoseconds / 1e3)

            timestamp = datetime.datetime.fromtimestamp(sec, tz=zone) + datetime.timedelta(
                microseconds=microseconds
            )

            ec_values = [
                _parse_val(row[idx], col_type, allow_null)
                for idx, col_type, allow_null in ec_extractors
            ]

            copy.write_row((station_id, timestamp, nanoseconds, *ec_values))
            ec_measurements_counter += 1

            if has_ch4 and ch4_sec_idx is not None:
                ch4_sec_raw = row[ch4_sec_idx].strip()
                if ch4_sec_raw and ch4_sec_raw not in ("-9999", "-9999.0", "nan"):
                    ch4_sec = int(ch4_sec_raw)
                    ch4_nano_raw = (
                        row[ch4_nano_idx].strip() if ch4_nano_idx is not None else ""
                    )
                    ch4_nano = (
                        int(ch4_nano_raw)
                        if ch4_nano_raw and ch4_nano_raw not in ("-9999", "-9999.0", "nan")
                        else nanoseconds
                    )
                    ch4_micro = round(ch4_nano / 1e3)
                    ch4_timestamp = datetime.datetime.fromtimestamp(
                        ch4_sec, tz=zone
                    ) + datetime.timedelta(microseconds=ch4_micro)

                    ch4_values = [
                        _parse_val(row[idx], col_type, allow_null)
                        for idx, col_type, allow_null in ch4_extractors
                    ]
                    ch4_rows.append((station_id, ch4_timestamp, ch4_nano, *ch4_values))

    # Bulk insert CH4 measurements if any were collected
    ch4_measurements_counter = len(ch4_rows)
    if ch4_rows:
        ch4_columns = [
            EddyCovarianceCh4Measurement.station_id.key,
            EddyCovarianceCh4Measurement.timestamp.key,
            EddyCovarianceCh4Measurement.nanoseconds.key,
        ] + [tbl_col for file_col, tbl_col, _ in CH4_COLUMNS if file_col in name_to_idx]

        with cursor.copy(
            f"COPY {EddyCovarianceCh4Measurement.__tablename__} ({', '.join(ch4_columns)}) FROM STDIN"
        ) as copy_ch4:
            for r in ch4_rows:
                copy_ch4.write_row(r)

    cursor.close()
    return ec_measurements_counter, ch4_measurements_counter


def _ingest_ghg_file(file: IO[bytes], session: Session, station_id: int) -> tuple[int, int]:
    "Reads sensor measurements from a GHG file and stores them in the database."
    with ZipFile(file) as ghg_file:
        data_file_name = None
        for name in ghg_file.namelist():
            if EDDY_COVARIANCE_DATA_FILE_NAME_REGEXP.match(name) and "biomet" not in name:
                data_file_name = name
                break

        if data_file_name is None:
            raise FileNotFoundError("Could not find data file in GHG file")

        with ghg_file.open(data_file_name) as data_file:
            text_file = TextIOWrapper(data_file, encoding="ASCII")
            return _ingest_eddy_covariance_data_stream(text_file, session, station_id)


def _worker_ingest_local_ec_file(
    file_path_str: str,
    canonical_path: str,
    station_id: int,
    server_id: int,
    database_url: str,
) -> IngestionResult:
    start_time = perf_counter()
    file_path = Path(file_path_str)
    try:
        engine = get_worker_engine(database_url)
        with Session(engine) as session:
            if file_path.suffix == ".data":
                with open(file_path, "r", encoding="ASCII") as text_file:
                    ec_cnt, ch4_cnt = _ingest_eddy_covariance_data_stream(
                        text_file, session, station_id
                    )
            else:
                with open(file_path, "rb") as file:
                    ec_cnt, ch4_cnt = _ingest_ghg_file(file, session, station_id)

            record_ingested_file(session, server_id, canonical_path, commit=False)
            session.commit()
            duration = perf_counter() - start_time
            return IngestionResult(
                path=canonical_path,
                success=True,
                rows_count=ec_cnt,
                extra_rows_count=ch4_cnt,
                duration=duration,
            )
    except Exception as e:
        logger.exception("Failed to ingest local file %s", file_path_str)
        return IngestionResult(
            path=canonical_path,
            success=False,
            duration=perf_counter() - start_time,
            error=str(e),
        )


_worker_sftp_client: SFTPClient | None = None
_worker_sftp_transport: Transport | None = None


def _get_worker_sftp(credentials: SFTPCredentials) -> SFTPClient:
    global _worker_sftp_client, _worker_sftp_transport
    if _worker_sftp_client is None:
        transport = Transport((credentials.host, credentials.port))
        transport.connect(
            None,
            username=credentials.username,
            password=credentials.password,
            pkey=credentials.private_key,
        )
        _worker_sftp_transport = transport
        _worker_sftp_client = SFTPClient.from_transport(transport)
    return _worker_sftp_client


def _worker_ingest_sftp_ec_file(
    remote_path: str,
    station_id: int,
    server_id: int,
    credentials: SFTPCredentials,
    database_url: str,
) -> IngestionResult:
    start_time = perf_counter()
    try:
        sftp = _get_worker_sftp(credentials)
        with sftp.file(remote_path, mode="rb", bufsize=16384) as file:
            file_contents = file.read()

        engine = get_worker_engine(database_url)
        with Session(engine) as session:
            buffer = BytesIO(file_contents)
            ec_cnt, ch4_cnt = _ingest_ghg_file(buffer, session, station_id)
            record_ingested_file(session, server_id, remote_path, commit=False)
            session.commit()
            duration = perf_counter() - start_time
            return IngestionResult(
                path=remote_path,
                success=True,
                rows_count=ec_cnt,
                extra_rows_count=ch4_cnt,
                duration=duration,
            )
    except Exception as e:
        logger.exception("Failed to ingest SFTP file %s", remote_path)
        return IngestionResult(
            path=remote_path,
            success=False,
            duration=perf_counter() - start_time,
            error=str(e),
        )


def ingest_eddy_covariance_data_from_local_path(
    path: Path,
    filters: EddyCovarianceDataFilters,
    engine: Engine,
    database_url: str | None = None,
    workers: int = 4,
) -> list[IngestionResult]:
    if not path.exists():
        raise FileNotFoundError(f"Path doesn't exist or isn't readable: '{path}'")

    db_url = database_url or engine.url.render_as_string(hide_password=False)
    server_id = get_or_create_local_server(engine)
    station_code_mapping = _EddyCovarianceStationCodeMapping(
        engine, filters.station_codes
    )

    candidate_files: list[Path] = []
    if path.is_file():
        candidate_files.append(path)
    elif path.is_dir():
        for file_path in path.rglob("*"):
            if not file_path.is_file():
                continue
            name = file_path.name
            if name.endswith((".ghg", ".data", ".ghg.zip")):
                candidate_files.append(file_path)
    else:
        raise ValueError("Path is neither a regular file, nor a directory")

    # Match metadata and apply initial filters
    pending_tasks: list[tuple[str, str, int]] = []
    for file_path in candidate_files:
        meta = _extract_eddy_covariance_file_metadata(file_path.name)
        if not meta:
            continue

        if filters.station_codes and meta.station_code not in filters.station_codes:
            continue

        if filters.from_timestamp and meta.timestamp < filters.from_timestamp:
            continue

        if filters.until_timestamp and meta.timestamp > filters.until_timestamp:
            continue

        station_id = station_code_mapping.get_id(meta.station_code)
        canonical = canonicalize_path(file_path)
        pending_tasks.append((str(file_path), canonical, station_id))

    if not pending_tasks:
        logger.info("No files matching provided criteria found at %s", path)
        return []

    logger.info("Discovered %d candidate files matching filters", len(pending_tasks))

    # Bulk deduplication
    with Session(engine) as session:
        already_ingested = get_already_ingested_paths(
            session, server_id, [c for _, c, _ in pending_tasks]
        )

    tasks_to_run = [
        (f_str, c_path, sid)
        for f_str, c_path, sid in pending_tasks
        if c_path not in already_ingested
    ]

    logger.info(
        "Files summary: %d total, %d already ingested, %d pending ingestion",
        len(pending_tasks),
        len(already_ingested),
        len(tasks_to_run),
    )

    if not tasks_to_run:
        return []

    results: list[IngestionResult] = []
    t_start = perf_counter()

    if workers <= 1 or len(tasks_to_run) == 1:
        logger.info("Ingesting %d files sequentially...", len(tasks_to_run))
        for f_str, c_path, sid in tasks_to_run:
            res = _worker_ingest_local_ec_file(f_str, c_path, sid, server_id, db_url)
            results.append(res)
            if res.success:
                logger.info(
                    "Ingested %s in %.2fs (%d EC rows, %d CH4 rows)",
                    Path(f_str).name,
                    res.duration,
                    res.rows_count,
                    res.extra_rows_count,
                )
            else:
                logger.error("Failed to ingest %s: %s", Path(f_str).name, res.error)
    else:
        actual_workers = min(workers, len(tasks_to_run))
        logger.info(
            "Ingesting %d files across %d worker processes...",
            len(tasks_to_run),
            actual_workers,
        )
        with ProcessPoolExecutor(max_workers=actual_workers) as executor:
            future_to_task = {
                executor.submit(
                    _worker_ingest_local_ec_file, f_str, c_path, sid, server_id, db_url
                ): f_str
                for f_str, c_path, sid in tasks_to_run
            }
            for future in as_completed(future_to_task):
                f_str = future_to_task[future]
                try:
                    res = future.result()
                    results.append(res)
                    if res.success:
                        logger.info(
                            "Ingested %s in %.2fs (%d EC rows, %d CH4 rows)",
                            Path(f_str).name,
                            res.duration,
                            res.rows_count,
                            res.extra_rows_count,
                        )
                    else:
                        logger.error("Failed to ingest %s: %s", Path(f_str).name, res.error)
                except Exception as exc:
                    logger.exception("Worker generated an unhandled exception for %s", f_str)
                    results.append(
                        IngestionResult(
                            path=canonicalize_path(f_str),
                            success=False,
                            error=str(exc),
                        )
                    )

    total_time = perf_counter() - t_start
    total_ec = sum(r.rows_count for r in results if r.success)
    total_ch4 = sum(r.extra_rows_count for r in results if r.success)
    successful = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)
    rate = total_ec / total_time if total_time > 0 else 0

    logger.info(
        "Eddy covariance ingestion complete: %d succeeded, %d failed in %.2fs "
        "(%d base EC rows, %d CH4 rows, %.1f rows/s)",
        successful,
        failed,
        total_time,
        total_ec,
        total_ch4,
        rate,
    )
    return results


def ingest_eddy_covariance_data_from_sftp_server(
    sftp_credentials: SFTPCredentials,
    filters: EddyCovarianceDataFilters,
    engine: Engine,
    sftp_base_path: str | None = None,
    database_url: str | None = None,
    workers: int = 4,
) -> list[IngestionResult]:
    db_url = database_url or engine.url.render_as_string(hide_password=False)
    station_code_mapping = _EddyCovarianceStationCodeMapping(
        engine, filters.station_codes
    )
    server_id = get_or_create_server(engine, sftp_credentials.host)

    logger.info("Connecting to SFTP server `%s:%d`...", sftp_credentials.host, sftp_credentials.port)
    transport = Transport((sftp_credentials.host, sftp_credentials.port))
    transport.connect(
        None,
        username=sftp_credentials.username,
        password=sftp_credentials.password,
        pkey=sftp_credentials.private_key,
    )
    sftp = SFTPClient.from_transport(transport)
    if sftp is None:
        raise ConnectionError("Failed to create SFTP client")

    base_path = f"/{sftp_base_path.removeprefix('/')}" if sftp_base_path else ""
    if base_path:
        logger.info("Changing directory to base path '%s'", base_path)
        sftp.chdir(base_path)

    entries = set(sftp.listdir())
    station_directory_names = station_code_mapping.get_all_sftp_directory_names()
    matched_entries = entries & station_directory_names
    if not matched_entries:
        raise FileNotFoundError(
            "No directories could be found which match registered eddy covariance station SFTP directory names"
        )

    logger.info("Found %d station directories matching criteria", len(matched_entries))

    # Discover candidate files across all matched station directories
    pending_tasks: list[tuple[str, int]] = []
    for entry in matched_entries:
        dir_path = f"{base_path}/{entry}" if base_path else entry
        sftp.chdir(dir_path)
        file_names = sftp.listdir()

        files_with_meta: list[tuple[str, _EddyCovarianceFileMetadata]] = []
        for file_name in file_names:
            meta = _extract_eddy_covariance_file_metadata(file_name)
            if meta:
                files_with_meta.append((file_name, meta))

        matched_files = list(_filter_files(files_with_meta, filters))
        for file_name, meta in matched_files:
            station_id = station_code_mapping.get_id(meta.station_code)
            full_remote_path = f"{dir_path}/{file_name}"
            pending_tasks.append((full_remote_path, station_id))

    sftp.close()
    transport.close()

    if not pending_tasks:
        logger.info("No files matching provided filters found on SFTP server")
        return []

    # Bulk deduplication
    with Session(engine) as session:
        already_ingested = get_already_ingested_paths(
            session, server_id, [p for p, _ in pending_tasks]
        )

    tasks_to_run = [
        (rem_path, sid)
        for rem_path, sid in pending_tasks
        if rem_path not in already_ingested
    ]

    logger.info(
        "SFTP files summary: %d total, %d already ingested, %d pending ingestion",
        len(pending_tasks),
        len(already_ingested),
        len(tasks_to_run),
    )

    if not tasks_to_run:
        return []

    results: list[IngestionResult] = []
    t_start = perf_counter()

    if workers <= 1 or len(tasks_to_run) == 1:
        logger.info("Ingesting %d SFTP files sequentially...", len(tasks_to_run))
        for rem_path, sid in tasks_to_run:
            res = _worker_ingest_sftp_ec_file(
                rem_path, sid, server_id, sftp_credentials, db_url
            )
            results.append(res)
    else:
        actual_workers = min(workers, len(tasks_to_run))
        logger.info(
            "Ingesting %d SFTP files across %d worker processes...",
            len(tasks_to_run),
            actual_workers,
        )
        with ProcessPoolExecutor(max_workers=actual_workers) as executor:
            future_to_task = {
                executor.submit(
                    _worker_ingest_sftp_ec_file,
                    rem_path,
                    sid,
                    server_id,
                    sftp_credentials,
                    db_url,
                ): rem_path
                for rem_path, sid in tasks_to_run
            }
            for future in as_completed(future_to_task):
                rem_path = future_to_task[future]
                try:
                    res = future.result()
                    results.append(res)
                    if res.success:
                        logger.info(
                            "Ingested %s in %.2fs (%d EC rows, %d CH4 rows)",
                            rem_path,
                            res.duration,
                            res.rows_count,
                            res.extra_rows_count,
                        )
                    else:
                        logger.error("Failed to ingest %s: %s", rem_path, res.error)
                except Exception as exc:
                    logger.exception("Worker unhandled exception for %s", rem_path)
                    results.append(
                        IngestionResult(
                            path=rem_path,
                            success=False,
                            error=str(exc),
                        )
                    )

    total_time = perf_counter() - t_start
    total_ec = sum(r.rows_count for r in results if r.success)
    total_ch4 = sum(r.extra_rows_count for r in results if r.success)
    successful = sum(1 for r in results if r.success)
    rate = total_ec / total_time if total_time > 0 else 0

    logger.info(
        "SFTP eddy covariance ingestion complete: %d succeeded, %d failed in %.2fs "
        "(%d base EC rows, %d CH4 rows, %.1f rows/s)",
        successful,
        len(results) - successful,
        total_time,
        total_ec,
        total_ch4,
        rate,
    )
    return results
