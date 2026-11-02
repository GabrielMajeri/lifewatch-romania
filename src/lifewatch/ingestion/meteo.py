import csv
import logging
import re
from collections.abc import Callable, Generator, Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from io import BytesIO, StringIO
from pathlib import Path
from time import perf_counter
from typing import IO

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from ..ftp import Fixed_FTP_TLS, FTPCredentials
from ..models import (
    Meteorological20SecondMeasurement,
    Meteorological30SecondMeasurement,
    Meteorological60SecondMeasurement,
    MeteorologicalStationInfo,
)
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


@dataclass
class _MeteorologicalFileMetadata:
    station_code: str
    date: date
    period: MeteorologicalVariablePeriodicity


_METEO_FILE_STATION_CODE_PATTERN = r"(?P<station_code>\w{2}-\w{3})"
_METEO_FILE_DATE_PATTERN = r"(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})"
_METEO_FILE_PERIOD_PATTERN = r"(?P<period>F\d{2})"

_METEO_FILE_PATTERN = re.compile(
    f"^{_METEO_FILE_STATION_CODE_PATTERN}_BM_{_METEO_FILE_DATE_PATTERN}_L\\d{{2}}_{_METEO_FILE_PERIOD_PATTERN}\\.csv$"
)


def _extract_meteo_file_metadata(file_name: str) -> _MeteorologicalFileMetadata | None:
    "Extract station code, record date and sampling period from a meteorological data file name."
    match = _METEO_FILE_PATTERN.fullmatch(file_name)
    if not match:
        return None

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

    return _MeteorologicalFileMetadata(
        station_code=match.group("station_code"), date=file_date, period=period
    )


def _filter_files(
    files_with_metadata: Iterable[tuple[str, _MeteorologicalFileMetadata]],
    filters: MeteorologicalDataFilters,
) -> Generator[tuple[str, _MeteorologicalFileMetadata]]:
    "Filter a list of weather station data files using the provided criteria."
    filter_by_date = filters.from_date is not None or filters.until_date is not None

    if not filters.station_codes and not filter_by_date and not filters.periodicities:
        yield from files_with_metadata
        return

    allowed_station_codes = (
        set(filters.station_codes) if filters.station_codes else None
    )
    allowed_periodicities = (
        set(filters.periodicities) if filters.periodicities else None
    )

    for file_name, meta in files_with_metadata:
        if allowed_periodicities and meta.period not in allowed_periodicities:
            continue

        if allowed_station_codes and meta.station_code not in allowed_station_codes:
            continue

        if filters.from_date is not None and meta.date < filters.from_date:
            continue

        if filters.until_date is not None and meta.date > filters.until_date:
            continue

        yield file_name, meta


class _MeteorologicalStationCodeMapping:
    _mapping: dict[str, int]

    def __init__(
        self, engine: Engine, selected_station_codes: list[str] | None = None
    ) -> None:
        selected_codes = selected_station_codes or []
        stmt = select(
            MeteorologicalStationInfo.station_id,
            MeteorologicalStationInfo.code,
        )

        if selected_codes:
            stmt = stmt.where(MeteorologicalStationInfo.code.in_(selected_codes))

        with Session(engine) as session:
            result = session.execute(stmt)
            station_ids_and_codes = result.all()

        if selected_codes and len(station_ids_and_codes) != len(selected_codes):
            logger.error(
                "Could not find stations corresponding to some of the provided station codes"
            )
            retrieved_station_codes = {code for (_, code) in station_ids_and_codes}
            missing_stations = set(selected_codes) - retrieved_station_codes
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
        station_id = self._mapping.get(station_code)
        if not station_id:
            raise KeyError(
                f"Could not find station with code '{station_code}' in list of stations retrieved from the database"
            )
        return station_id


def _ingest_meteorological_data(
    buffer: IO,
    session: Session,
    station_id: int,
    column_name_mapping: list[tuple[str, str, bool]],
    destination_table: type[
        Meteorological20SecondMeasurement
        | Meteorological30SecondMeasurement
        | Meteorological60SecondMeasurement
    ],
    process_column_value: Callable[[str, str | None], str | None] | None = None,
) -> int:
    csv_reader = csv.reader(buffer, delimiter=",")
    fieldnames = next(csv_reader, None)
    if not fieldnames:
        raise ValueError(
            "Could not read variable names (column headers) from data file"
        )

    field_to_idx = {name: i for i, name in enumerate(fieldnames)}
    if "TIMESTAMP" not in field_to_idx:
        raise ValueError("CSV is missing required TIMESTAMP column")

    ts_idx = field_to_idx["TIMESTAMP"]

    missing_required = [
        csv_col
        for csv_col, _, required in column_name_mapping
        if required and csv_col not in field_to_idx
    ]
    if missing_required:
        raise ValueError(
            f"CSV is missing some required columns: {', '.join(missing_required)}"
        )

    extractors = [
        (
            field_to_idx.get(csv_col),
            csv_col,
            process_column_value,
        )
        for csv_col, _, _ in column_name_mapping
    ]

    columns = [
        destination_table.station_id.key,
        destination_table.timestamp.key,
    ] + [table_column_name for (_, table_column_name, _) in column_name_mapping]

    romania_time_zone = timezone(timedelta(hours=+2))
    num_rows = 0

    cursor = session.connection().connection.cursor()
    with cursor.copy(
        f"COPY {destination_table.__tablename__} ({', '.join(columns)}) FROM STDIN"
    ) as copy:
        for row in csv_reader:
            if not row:
                continue
            timestamp = datetime.fromisoformat(row[ts_idx]).replace(
                tzinfo=romania_time_zone
            )

            measurement_values = []
            for idx, col_name, proc_fn in extractors:
                if idx is not None and idx < len(row):
                    val = row[idx]
                else:
                    val = None

                if proc_fn is not None:
                    val = proc_fn(col_name, val)

                measurement_values.append(val)

            copy.write_row((station_id, timestamp, *measurement_values))
            num_rows += 1

    cursor.close()
    return num_rows


def _process_20s_column_value(column_name: str, value: str | None) -> str | None:
    if column_name == "Error_Code_SR30" and value:
        try:
            return str(int(float(value)))
        except ValueError, TypeError:
            return value
    return value


def _ingest_20_seconds_periodicity_meteorological_data(
    buffer: IO, session: Session, station_id: int
) -> int:
    column_name_mapping: list[tuple[str, str, bool]] = [
        ("Irradiance", "irradiance", True),
        ("Unit_Temp_SR30", "unit_temperature_sr30", True),
        ("Error_Code_SR30", "error_code_sr30", False),
        ("LICOR_Irradiance", "licor_irradiance", False),
        ("CUV5_Irradiance", "cuv5_irradiance", False),
    ]
    return _ingest_meteorological_data(
        buffer,
        session,
        station_id,
        column_name_mapping,
        Meteorological20SecondMeasurement,
        _process_20s_column_value,
    )


def _process_30s_column_value(column_name: str, value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if not v or v.lower() in ("nan", "-nan", "null"):
        return None

    if column_name in (
        "Wind_Direction",
        "Present_Weather_Code",
        "Visibility",
        "Particles_Count",
    ):
        try:
            return str(int(float(v)))
        except ValueError, TypeError:
            return v
    return v


def _ingest_30_seconds_periodicity_meteorological_data(
    buffer: IO, session: Session, station_id: int
) -> int:
    column_name_mapping: list[tuple[str, str, bool]] = [
        ("Wind_Direction", "wind_direction", True),
        ("Wind_Speed", "wind_speed", True),
        ("Present_Weather_Code", "current_weather_code", True),
        ("Visibility", "visibility", True),
        ("Particles_Count", "particles_count", True),
        ("Intensity", "intensity", True),
        ("Air_Temp_CS125", "air_temp_cs125", True),
        ("Air_Pressure", "air_pressure", True),
        ("Air_Humidity", "air_humidity", True),
        ("Air_Temperature", "air_temperature", True),
        ("DataLogger_Voltage", "data_logger_voltage", False),
    ]
    return _ingest_meteorological_data(
        buffer,
        session,
        station_id,
        column_name_mapping,
        Meteorological30SecondMeasurement,
        _process_30s_column_value,
    )


def _process_60s_column_value(column_name: str, value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if not v or v.lower() in ("nan", "-nan", "null"):
        return None

    if column_name == "Measurement_Quality_SR50A":
        try:
            return str(int(float(v)))
        except ValueError, TypeError:
            return v
    return v


def _ingest_60_seconds_periodicity_meteorological_data(
    buffer: IO, session: Session, station_id: int
) -> int:
    column_name_mapping: list[tuple[str, str, bool]] = [
        ("Snow_Depth", "snow_depth", True),
        ("Total_Rain", "total_rain", True),
        ("Soil_Temperature", "soil_temperature", True),
        ("Soil_Humidity", "soil_humidity", True),
        ("Measurement_Quality_SR50A", "measurement_quality_sr50a", True),
    ]
    return _ingest_meteorological_data(
        buffer,
        session,
        station_id,
        column_name_mapping,
        Meteorological60SecondMeasurement,
        _process_60s_column_value,
    )


def _ingest_meteo_stream_by_period(
    period: str, buffer: IO, session: Session, station_id: int
) -> int:
    if period == MeteorologicalVariablePeriodicity.EVERY_20_SECONDS or period == "20":
        return _ingest_20_seconds_periodicity_meteorological_data(
            buffer, session, station_id
        )
    elif period == MeteorologicalVariablePeriodicity.EVERY_30_SECONDS or period == "30":
        return _ingest_30_seconds_periodicity_meteorological_data(
            buffer, session, station_id
        )
    elif period == MeteorologicalVariablePeriodicity.EVERY_60_SECONDS or period == "60":
        return _ingest_60_seconds_periodicity_meteorological_data(
            buffer, session, station_id
        )
    else:
        raise NotImplementedError(f"Unsupported periodicity: {period}")


def _worker_ingest_local_meteo_file(
    file_path_str: str,
    canonical_path: str,
    station_id: int,
    period: str,
    server_id: int,
    database_url: str,
) -> IngestionResult:
    start_time = perf_counter()
    file_path = Path(file_path_str)
    try:
        engine = get_worker_engine(database_url)
        with Session(engine) as session:
            with open(file_path, "r", encoding="utf-8") as file:
                rows_count = _ingest_meteo_stream_by_period(
                    period, file, session, station_id
                )

            record_ingested_file(session, server_id, canonical_path, commit=False)
            session.commit()
            duration = perf_counter() - start_time
            return IngestionResult(
                path=canonical_path,
                success=True,
                rows_count=rows_count,
                duration=duration,
            )
    except Exception as e:
        logger.exception("Failed to ingest local meteo file %s", file_path_str)
        return IngestionResult(
            path=canonical_path,
            success=False,
            duration=perf_counter() - start_time,
            error=str(e),
        )


_worker_ftp_client: Fixed_FTP_TLS | None = None


def _get_worker_ftp(credentials: FTPCredentials) -> Fixed_FTP_TLS:
    global _worker_ftp_client
    if _worker_ftp_client is None:
        ftp = Fixed_FTP_TLS()
        ftp.connect(credentials.host, credentials.port, timeout=10)
        ftp.auth()
        ftp.login(credentials.user, credentials.password)
        ftp.prot_p()
        _worker_ftp_client = ftp
    return _worker_ftp_client


def _worker_ingest_ftp_meteo_file(
    remote_path: str,
    station_id: int,
    period: str,
    server_id: int,
    credentials: FTPCredentials,
    database_url: str,
) -> IngestionResult:
    start_time = perf_counter()
    try:
        ftp = _get_worker_ftp(credentials)
        buffer = BytesIO()
        ftp.retrbinary(f"RETR {remote_path}", buffer.write)
        file_contents = buffer.getvalue().decode("utf-8")
        string_io = StringIO(file_contents)

        engine = get_worker_engine(database_url)
        with Session(engine) as session:
            rows_count = _ingest_meteo_stream_by_period(
                period, string_io, session, station_id
            )
            record_ingested_file(session, server_id, remote_path, commit=False)
            session.commit()
            duration = perf_counter() - start_time
            return IngestionResult(
                path=remote_path,
                success=True,
                rows_count=rows_count,
                duration=duration,
            )
    except Exception as e:
        logger.exception("Failed to ingest FTP meteo file %s", remote_path)
        return IngestionResult(
            path=remote_path,
            success=False,
            duration=perf_counter() - start_time,
            error=str(e),
        )


def ingest_meteo_data_from_local_path(
    path: Path,
    filters: MeteorologicalDataFilters,
    engine: Engine,
    database_url: str | None = None,
    workers: int = 4,
) -> list[IngestionResult]:
    if not path.exists():
        raise FileNotFoundError(f"Path doesn't exist or isn't readable: '{path}'")

    db_url = database_url or engine.url.render_as_string(hide_password=False)
    server_id = get_or_create_local_server(engine)
    station_code_mapping = _MeteorologicalStationCodeMapping(
        engine, filters.station_codes
    )

    candidate_files: list[Path] = []
    if path.is_file():
        candidate_files.append(path)
    elif path.is_dir():
        for file_path in path.rglob("*"):
            if file_path.is_file() and file_path.suffix == ".csv":
                candidate_files.append(file_path)
    else:
        raise ValueError("Path is neither a regular file, nor a directory")

    pending_tasks: list[tuple[str, str, int, str]] = []
    for file_path in candidate_files:
        metadata = _extract_meteo_file_metadata(file_path.name)
        if not metadata:
            continue

        if filters.periodicities and metadata.period not in filters.periodicities:
            continue

        if filters.station_codes and metadata.station_code not in filters.station_codes:
            continue

        if filters.from_date and metadata.date < filters.from_date:
            continue

        if filters.until_date and metadata.date > filters.until_date:
            continue

        station_id = station_code_mapping.get(metadata.station_code)
        canonical = canonicalize_path(file_path)
        pending_tasks.append(
            (str(file_path), canonical, station_id, metadata.period.value)
        )

    if not pending_tasks:
        logger.info("No files matching provided criteria found at %s", path)
        return []

    logger.info("Discovered %d candidate files matching filters", len(pending_tasks))

    # Bulk deduplication
    with Session(engine) as session:
        already_ingested = get_already_ingested_paths(
            session, server_id, [c for _, c, _, _ in pending_tasks]
        )

    tasks_to_run = [
        (f_str, c_path, sid, p_val)
        for f_str, c_path, sid, p_val in pending_tasks
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
        logger.info(
            "Ingesting %d meteorological files sequentially...", len(tasks_to_run)
        )
        for f_str, c_path, sid, p_val in tasks_to_run:
            res = _worker_ingest_local_meteo_file(
                f_str, c_path, sid, p_val, server_id, db_url
            )
            results.append(res)
            if res.success:
                logger.info(
                    "Ingested %s in %.2fs (%d rows)",
                    Path(f_str).name,
                    res.duration,
                    res.rows_count,
                )
            else:
                logger.error("Failed to ingest %s: %s", Path(f_str).name, res.error)
    else:
        actual_workers = min(workers, len(tasks_to_run))
        logger.info(
            "Ingesting %d meteorological files across %d worker processes...",
            len(tasks_to_run),
            actual_workers,
        )
        with ProcessPoolExecutor(max_workers=actual_workers) as executor:
            future_to_task = {
                executor.submit(
                    _worker_ingest_local_meteo_file,
                    f_str,
                    c_path,
                    sid,
                    p_val,
                    server_id,
                    db_url,
                ): f_str
                for f_str, c_path, sid, p_val in tasks_to_run
            }
            for future in as_completed(future_to_task):
                f_str = future_to_task[future]
                try:
                    res = future.result()
                    results.append(res)
                    if res.success:
                        logger.info(
                            "Ingested %s in %.2fs (%d rows)",
                            Path(f_str).name,
                            res.duration,
                            res.rows_count,
                        )
                    else:
                        logger.error(
                            "Failed to ingest %s: %s", Path(f_str).name, res.error
                        )
                except Exception as exc:
                    logger.exception(
                        "Worker generated an unhandled exception for %s", f_str
                    )
                    results.append(
                        IngestionResult(
                            path=canonicalize_path(f_str),
                            success=False,
                            error=str(exc),
                        )
                    )

    total_time = perf_counter() - t_start
    total_rows = sum(r.rows_count for r in results if r.success)
    successful = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)
    rate = total_rows / total_time if total_time > 0 else 0

    logger.info(
        "Meteorological ingestion complete: %d succeeded, %d failed in %.2fs (%d rows, %.1f rows/s)",
        successful,
        failed,
        total_time,
        total_rows,
        rate,
    )
    return results


def ingest_meteo_data_from_ftp_server(
    ftp_credentials: FTPCredentials,
    filters: MeteorologicalDataFilters,
    engine: Engine,
    database_url: str | None = None,
    workers: int = 4,
) -> list[IngestionResult]:
    db_url = database_url or engine.url.render_as_string(hide_password=False)
    station_code_mapping = _MeteorologicalStationCodeMapping(
        engine, selected_station_codes=filters.station_codes
    )
    server_id = get_or_create_server(engine, ftp_credentials.host)

    logger.info(
        "Connecting to FTP server `%s:%d`...",
        ftp_credentials.host,
        ftp_credentials.port,
    )

    pending_tasks: list[tuple[str, int, str]] = []

    with Fixed_FTP_TLS() as ftp:
        ftp.connect(ftp_credentials.host, ftp_credentials.port, timeout=10)
        ftp.auth()
        ftp.login(ftp_credentials.user, ftp_credentials.password)
        ftp.prot_p()

        root_entries = ftp.nlst()
        processed_data_dir = "meteo_procesat"
        if processed_data_dir not in root_entries:
            raise RuntimeError(
                f"Processed meteorological data directory '{processed_data_dir}' not found in FTP share root"
            )

        ftp.cwd(processed_data_dir)
        subdirs = ftp.nlst()

        periodicity_dirs = []
        if (
            MeteorologicalVariablePeriodicity.EVERY_20_SECONDS in filters.periodicities
            and "20" in subdirs
        ):
            periodicity_dirs.append(
                ("20", MeteorologicalVariablePeriodicity.EVERY_20_SECONDS.value)
            )
        if (
            MeteorologicalVariablePeriodicity.EVERY_30_SECONDS in filters.periodicities
            and "30" in subdirs
        ):
            periodicity_dirs.append(
                ("30", MeteorologicalVariablePeriodicity.EVERY_30_SECONDS.value)
            )
        if (
            MeteorologicalVariablePeriodicity.EVERY_60_SECONDS in filters.periodicities
            and "60" in subdirs
        ):
            periodicity_dirs.append(
                ("60", MeteorologicalVariablePeriodicity.EVERY_60_SECONDS.value)
            )

        for dir_name, period_val in periodicity_dirs:
            ftp.cwd(dir_name)
            data_files = ftp.nlst()
            files_metadata = [
                (fn, _extract_meteo_file_metadata(fn)) for fn in data_files
            ]
            files_with_metadata = [(fn, m) for fn, m in files_metadata if m is not None]
            matched = list(_filter_files(files_with_metadata, filters))

            for file_name, meta in matched:
                station_id = station_code_mapping.get(meta.station_code)
                remote_path = f"/{processed_data_dir}/{dir_name}/{file_name}"
                pending_tasks.append((remote_path, station_id, period_val))

            ftp.cwd("..")

    if not pending_tasks:
        logger.info("No meteorological files found matching filters on FTP server")
        return []

    # Bulk deduplication
    with Session(engine) as session:
        already_ingested = get_already_ingested_paths(
            session, server_id, [p for p, _, _ in pending_tasks]
        )

    tasks_to_run = [
        (rem_path, sid, p_val)
        for rem_path, sid, p_val in pending_tasks
        if rem_path not in already_ingested
    ]

    logger.info(
        "FTP files summary: %d total, %d already ingested, %d pending ingestion",
        len(pending_tasks),
        len(already_ingested),
        len(tasks_to_run),
    )

    if not tasks_to_run:
        return []

    results: list[IngestionResult] = []
    t_start = perf_counter()

    if workers <= 1 or len(tasks_to_run) == 1:
        logger.info("Ingesting %d FTP files sequentially...", len(tasks_to_run))
        for rem_path, sid, p_val in tasks_to_run:
            res = _worker_ingest_ftp_meteo_file(
                rem_path, sid, p_val, server_id, ftp_credentials, db_url
            )
            results.append(res)
    else:
        actual_workers = min(workers, len(tasks_to_run))
        logger.info(
            "Ingesting %d FTP files across %d worker processes...",
            len(tasks_to_run),
            actual_workers,
        )
        with ProcessPoolExecutor(max_workers=actual_workers) as executor:
            future_to_task = {
                executor.submit(
                    _worker_ingest_ftp_meteo_file,
                    rem_path,
                    sid,
                    p_val,
                    server_id,
                    ftp_credentials,
                    db_url,
                ): rem_path
                for rem_path, sid, p_val in tasks_to_run
            }
            for future in as_completed(future_to_task):
                rem_path = future_to_task[future]
                try:
                    res = future.result()
                    results.append(res)
                    if res.success:
                        logger.info(
                            "Ingested %s in %.2fs (%d rows)",
                            rem_path,
                            res.duration,
                            res.rows_count,
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
    total_rows = sum(r.rows_count for r in results if r.success)
    successful = sum(1 for r in results if r.success)
    rate = total_rows / total_time if total_time > 0 else 0

    logger.info(
        "FTP meteorological ingestion complete: %d succeeded, %d failed in %.2fs (%d rows, %.1f rows/s)",
        successful,
        len(results) - successful,
        total_time,
        total_rows,
        rate,
    )
    return results
