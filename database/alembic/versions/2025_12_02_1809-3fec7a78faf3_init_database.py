"""Initialize database

Revision ID: 3fec7a78faf3
Revises:
Create Date: 2025-12-02 18:09:03.111753

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "3fec7a78faf3"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sensor_types",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("name", name="sensor_types_name_unique"),
    )
    op.create_table(
        "stations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("extra", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="stations_name_unique"),
    )
    op.create_table(
        "sensors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("station_id", sa.Integer(), nullable=False),
        sa.Column("sensor_type_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("serial_number", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["sensor_type_id"],
            ["sensor_types.id"],
        ),
        sa.ForeignKeyConstraint(
            ["station_id"],
            ["stations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "sensor_type_id",
            "serial_number",
            name="sensors_serial_number_unique_per_type",
        ),
    )
    op.create_table(
        "eddy_covariance_measurements",
        sa.Column("sensor_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("nanoseconds", sa.Integer(), nullable=False),
        sa.Column("co2_concentration", sa.Float(precision=6), nullable=False),
        sa.Column("h2o_concentration", sa.Float(precision=6), nullable=False),
        sa.Column(
            "co2_raw_power_in_sample_wavelength", sa.Float(precision=6), nullable=False
        ),
        sa.Column(
            "h2o_raw_power_in_sample_wavelength", sa.Float(precision=6), nullable=False
        ),
        sa.Column(
            "co2_raw_power_in_reference_wavelength",
            sa.Float(precision=6),
            nullable=False,
        ),
        sa.Column(
            "h2o_raw_power_in_reference_wavelength",
            sa.Float(precision=6),
            nullable=False,
        ),
        sa.Column("co2_molar_fraction", sa.Float(precision=6), nullable=False),
        sa.Column("h2o_molar_fraction", sa.Float(precision=6), nullable=False),
        sa.Column("co2_dry_mole_fraction", sa.Float(precision=6), nullable=False),
        sa.Column("h2o_dry_mole_fraction", sa.Float(precision=6), nullable=False),
        sa.Column("co2_mass_density", sa.Float(precision=6), nullable=False),
        sa.Column("h2o_mass_density", sa.Float(precision=6), nullable=False),
        sa.Column("co2_absorptance", sa.Float(precision=6), nullable=False),
        sa.Column("h2o_absorptance", sa.Float(precision=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["sensor_id"],
            ["sensors.id"],
        ),
        sa.PrimaryKeyConstraint("sensor_id", "timestamp"),
    )

    op.execute(
        "SELECT create_hypertable('eddy_covariance_measurements', by_range('timestamp', INTERVAL '1 day'))"
    )
    op.execute("""ALTER TABLE eddy_covariance_measurements SET (
        tsdb.enable_columnstore = true,
        tsdb.segmentby = 'sensor_id'
    )""")
    op.execute(
        "CALL add_columnstore_policy('eddy_covariance_measurements', after => INTERVAL '1 day')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("eddy_covariance_measurements")
    op.drop_table("sensors")
    op.drop_table("stations")
    op.drop_table("sensor_types")
