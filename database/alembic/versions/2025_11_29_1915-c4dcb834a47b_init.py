"""Initialize database

Revision ID: c4dcb834a47b
Revises:
Create Date: 2025-11-29 19:15:37.974983

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4dcb834a47b"
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
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="stations_name_unique"),
    )
    op.create_table(
        "variables",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("units", sa.String(), nullable=False),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="variables_name_unique"),
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
        "measurements",
        sa.Column("sensor_id", sa.Integer(), nullable=False),
        sa.Column("variable_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("nanoseconds", sa.Integer(), nullable=False),
        sa.Column("value", sa.Float(6), nullable=False),
        sa.ForeignKeyConstraint(
            ["sensor_id"],
            ["sensors.id"],
        ),
        sa.ForeignKeyConstraint(
            ["variable_id"],
            ["variables.id"],
        ),
        sa.PrimaryKeyConstraint("sensor_id", "variable_id", "timestamp"),
    )

    op.execute(
        "SELECT create_hypertable('measurements', by_range('timestamp', INTERVAL '1 day'))"
    )
    op.execute("""ALTER TABLE measurements SET (
        tsdb.enable_columnstore = true,
        tsdb.segmentby = 'sensor_id, variable_id'
    )""")
    op.execute("CALL add_columnstore_policy('measurements', after => INTERVAL '1 day')")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("measurements")
    op.drop_table("sensors")
    op.drop_table("variables")
    op.drop_table("stations")
    op.drop_table("sensor_types")
