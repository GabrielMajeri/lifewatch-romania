"""Initial migration

Revision ID: aa90e53a3838
Revises:
Create Date: 2026-11-02 09:56:00.030342
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa

from alembic import op

revision: str = "aa90e53a3838"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        "servers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hostname", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("hostname", name="servers_name_unique"),
    )
    op.create_table(
        "stations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "location",
            geoalchemy2.types.Geography(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                from_text="ST_GeogFromText",
                name="geography",
                nullable=False,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="stations_name_unique"),
    )
    op.create_table(
        "eddy_covariance_ch4_measurements",
        sa.Column("nanoseconds", sa.Integer(), nullable=False),
        sa.Column("ch4_molar_fraction", sa.Float(precision=6), nullable=True),
        sa.Column("ch4_molar_density", sa.Float(precision=6), nullable=True),
        sa.Column("ch4_temperature", sa.Float(precision=6), nullable=True),
        sa.Column("ch4_pressure", sa.Float(precision=6), nullable=True),
        sa.Column("ch4_signal_strength", sa.Float(precision=6), nullable=True),
        sa.Column("ch4_auxiliary_input_1", sa.Float(precision=6), nullable=True),
        sa.Column("ch4_auxiliary_input_2", sa.Float(precision=6), nullable=True),
        sa.Column("ch4_auxiliary_input_3", sa.Float(precision=6), nullable=True),
        sa.Column("ch4_auxiliary_input_4", sa.Float(precision=6), nullable=True),
        sa.Column("ch4_thermocouple_input_1", sa.Float(precision=6), nullable=True),
        sa.Column("ch4_thermocouple_input_2", sa.Float(precision=6), nullable=True),
        sa.Column("ch4_thermocouple_input_3", sa.Float(precision=6), nullable=True),
        sa.Column("ch4_diagnostic_value", sa.Integer(), nullable=False),
        sa.Column("ch4_drop_rate", sa.Float(precision=6), nullable=True),
        sa.Column("station_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["stations.id"],
        ),
        sa.PrimaryKeyConstraint("station_id", "timestamp"),
    )
    op.create_table(
        "eddy_covariance_measurements",
        sa.Column("nanoseconds", sa.Integer(), nullable=False),
        sa.Column("sequence_number", sa.BigInteger(), nullable=False),
        sa.Column("diagnostic_value", sa.SmallInteger(), nullable=False),
        sa.Column("diagnostic_value_2", sa.SmallInteger(), nullable=False),
        sa.Column("ds_diagnostic_value", sa.SmallInteger(), nullable=False),
        sa.Column("co2_concentration", sa.Float(precision=6), nullable=True),
        sa.Column("h2o_concentration", sa.Float(precision=6), nullable=True),
        sa.Column(
            "co2_raw_power_in_sample_wavelength", sa.Float(precision=6), nullable=True
        ),
        sa.Column(
            "h2o_raw_power_in_sample_wavelength", sa.Float(precision=6), nullable=True
        ),
        sa.Column(
            "co2_raw_power_in_reference_wavelength",
            sa.Float(precision=6),
            nullable=True,
        ),
        sa.Column(
            "h2o_raw_power_in_reference_wavelength",
            sa.Float(precision=6),
            nullable=True,
        ),
        sa.Column("co2_molar_fraction", sa.Float(precision=6), nullable=True),
        sa.Column("h2o_molar_fraction", sa.Float(precision=6), nullable=True),
        sa.Column("co2_mass_density", sa.Float(precision=6), nullable=True),
        sa.Column("h2o_mass_density", sa.Float(precision=6), nullable=True),
        sa.Column("co2_absorptance", sa.Float(precision=6), nullable=True),
        sa.Column("h2o_absorptance", sa.Float(precision=6), nullable=True),
        sa.Column("co2_signal_strength", sa.Float(precision=6), nullable=True),
        sa.Column("temperature", sa.Float(precision=6), nullable=True),
        sa.Column("pressure", sa.Float(precision=6), nullable=True),
        sa.Column("dew_point", sa.Float(precision=6), nullable=True),
        sa.Column("cooler_voltage", sa.Float(precision=6), nullable=True),
        sa.Column("chopper_cooler_voltage", sa.Float(precision=6), nullable=True),
        sa.Column("vin_smart_flux", sa.Float(precision=6), nullable=True),
        sa.Column("vin_dsi", sa.Float(precision=6), nullable=True),
        sa.Column("wind_u", sa.Float(precision=6), nullable=True),
        sa.Column("wind_v", sa.Float(precision=6), nullable=True),
        sa.Column("wind_w", sa.Float(precision=6), nullable=True),
        sa.Column("sonic_temperature", sa.Float(precision=6), nullable=True),
        sa.Column("anemometer_diagnostics", sa.SmallInteger(), nullable=False),
        sa.Column("chk", sa.SmallInteger(), nullable=False),
        sa.Column("station_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["stations.id"],
        ),
        sa.PrimaryKeyConstraint("station_id", "timestamp"),
    )
    op.create_table(
        "eddy_covariance_station_infos",
        sa.Column("station_id", sa.Integer(), nullable=False),
        sa.Column("sftp_directory_name", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("has_ch4", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["stations.id"],
        ),
        sa.PrimaryKeyConstraint("station_id"),
        sa.UniqueConstraint("code", name="eddy_covariance_station_infos_code_unique"),
        sa.UniqueConstraint(
            "sftp_directory_name",
            name="eddy_covariance_station_infos_sftp_directory_name_unique",
        ),
    )
    op.create_table(
        "ingested_files",
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["servers.id"],
        ),
        sa.PrimaryKeyConstraint("server_id", "path"),
    )
    op.create_index(
        "ingested_files_server_id", "ingested_files", ["server_id"], unique=False
    )
    op.create_table(
        "meteorological_20s_measurements",
        sa.Column("irradiance", sa.Float(precision=6), nullable=False),
        sa.Column("unit_temperature_sr30", sa.Float(precision=6), nullable=False),
        sa.Column("error_code_sr30", sa.SmallInteger(), nullable=False),
        sa.Column("licor_irradiance", sa.Float(precision=6), nullable=True),
        sa.Column("cuv5_irradiance", sa.Float(precision=6), nullable=True),
        sa.Column("station_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["stations.id"],
        ),
        sa.PrimaryKeyConstraint("station_id", "timestamp"),
    )
    op.create_table(
        "meteorological_30s_measurements",
        sa.Column("wind_direction", sa.SmallInteger(), nullable=True),
        sa.Column("wind_speed", sa.Float(), nullable=False),
        sa.Column("current_weather_code", sa.SmallInteger(), nullable=False),
        sa.Column("visibility", sa.Integer(), nullable=False),
        sa.Column("particles_count", sa.SmallInteger(), nullable=False),
        sa.Column("intensity", sa.Float(precision=6), nullable=False),
        sa.Column("air_temp_cs125", sa.Float(precision=6), nullable=False),
        sa.Column("air_pressure", sa.Float(precision=6), nullable=False),
        sa.Column("air_humidity", sa.Float(precision=6), nullable=False),
        sa.Column("air_temperature", sa.Float(precision=6), nullable=False),
        sa.Column("data_logger_voltage", sa.Float(precision=6), nullable=False),
        sa.Column("station_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["stations.id"],
        ),
        sa.PrimaryKeyConstraint("station_id", "timestamp"),
    )
    op.create_table(
        "meteorological_60s_measurements",
        sa.Column("snow_depth", sa.Float(precision=6), nullable=False),
        sa.Column("total_rain", sa.Float(precision=6), nullable=False),
        sa.Column("soil_temperature", sa.Float(precision=6), nullable=False),
        sa.Column("soil_humidity", sa.Float(precision=6), nullable=False),
        sa.Column("measurement_quality_sr50a", sa.SmallInteger(), nullable=False),
        sa.Column("station_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["stations.id"],
        ),
        sa.PrimaryKeyConstraint("station_id", "timestamp"),
    )
    op.create_table(
        "meteorological_station_infos",
        sa.Column("station_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["stations.id"],
        ),
        sa.PrimaryKeyConstraint("station_id"),
        sa.UniqueConstraint("code", name="meteorological_station_infos_code_unique"),
    )

    measurement_tables = [
        "meteorological_20s_measurements",
        "meteorological_30s_measurements",
        "meteorological_60s_measurements",
        "eddy_covariance_measurements",
        "eddy_covariance_ch4_measurements",
    ]

    for table_name in measurement_tables:
        op.execute(
            f"SELECT create_hypertable('{table_name}', by_range('timestamp', INTERVAL '1 day'))"
        )
        op.execute(f"""ALTER TABLE {table_name} SET (
            tsdb.enable_columnstore = true,
            tsdb.segmentby = 'station_id'
        );""")
        op.execute(
            f"CALL add_columnstore_policy('{table_name}', after => INTERVAL '1 day')"
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("meteorological_station_infos")
    op.drop_table("meteorological_60s_measurements")
    op.drop_table("meteorological_30s_measurements")
    op.drop_table("meteorological_20s_measurements")
    op.drop_index("ingested_files_server_id", table_name="ingested_files")
    op.drop_table("ingested_files")
    op.drop_table("eddy_covariance_station_infos")
    op.drop_table("eddy_covariance_measurements")
    op.drop_table("eddy_covariance_ch4_measurements")
    op.drop_table("stations")
    op.drop_table("servers")
