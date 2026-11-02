from lifewatch.ingestion.eddy_covariance import (
    _extract_eddy_covariance_file_metadata,
    _parse_val,
)
from lifewatch.ingestion.meteo import (
    MeteorologicalVariablePeriodicity,
    _extract_meteo_file_metadata,
)


def test_parse_val_numbers() -> None:
    "Parse some real numbers"
    assert _parse_val("12.34", float) == 12.34
    assert _parse_val("42", int) == 42
    assert _parse_val("42.0", int) == 42


def test_parse_val_missing_and_nulls() -> None:
    "Parse missing and null values"
    assert _parse_val("-9999", float) is None
    assert _parse_val("-9999.0", float) is None
    assert _parse_val("nan", float) is None
    assert _parse_val("NAN", float) is None
    assert _parse_val("null", float) is None
    assert _parse_val("", float) is None
    assert _parse_val(None, float) is None

    # Disallow null -> return 0 for non-null integer columns
    assert _parse_val("-9999", int, allow_null=False) == 0
    assert _parse_val("", int, allow_null=False) == 0


def test_ec_metadata_extraction():
    name = "2025-08-04T073000_smart3-01014.ghg"
    meta = _extract_eddy_covariance_file_metadata(name)
    assert meta is not None
    assert meta.station_code == "01014"
    assert meta.timestamp.year == 2025
    assert meta.timestamp.month == 8
    assert meta.timestamp.day == 4
    assert meta.timestamp.hour == 7
    assert meta.timestamp.minute == 30


def test_meteo_metadata_extraction():
    name = "AG-Dmb_BM_20261001_L01_F01.csv"
    meta = _extract_meteo_file_metadata(name)
    assert meta is not None
    assert meta.station_code == "AG-Dmb"
    assert meta.date.year == 2026
    assert meta.date.month == 10
    assert meta.date.day == 1
    assert meta.period == MeteorologicalVariablePeriodicity.EVERY_20_SECONDS
