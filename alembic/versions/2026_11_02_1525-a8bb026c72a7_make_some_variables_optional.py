"""Make some variables optional

Revision ID: a8bb026c72a7
Revises: aa90e53a3838
Create Date: 2026-11-02 15:25:45.194509
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8bb026c72a7"
down_revision: str | Sequence[str] | None = "aa90e53a3838"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    "Upgrade schema."
    op.alter_column(
        "meteorological_30s_measurements",
        "wind_speed",
        existing_type=sa.Float(),
        nullable=True,
    )
    op.alter_column(
        "meteorological_30s_measurements",
        "air_humidity",
        existing_type=sa.Float(precision=6),
        nullable=True,
    )
    op.alter_column(
        "meteorological_60s_measurements",
        "snow_depth",
        existing_type=sa.Float(precision=6),
        nullable=True,
    )


def downgrade() -> None:
    "Downgrade schema."
    op.alter_column(
        "meteorological_60s_measurements",
        "snow_depth",
        existing_type=sa.Float(precision=6),
        nullable=False,
    )
    op.alter_column(
        "meteorological_30s_measurements",
        "air_humidity",
        existing_type=sa.Float(precision=6),
        nullable=False,
    )
    op.alter_column(
        "meteorological_30s_measurements",
        "wind_speed",
        existing_type=sa.Float(),
        nullable=False,
    )
