from typing import Any

from sqlalchemy import JSON, create_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = "postgresql://dev:pwd@localhost:5432/lifewatch"

engine = create_engine(DATABASE_URL)


class Base(DeclarativeBase):
    type_annotation_map = {
        # Add support for JSON columns (for storing entity metadata).
        #
        # Based on https://stackoverflow.com/a/75678968
        dict[str, Any]: JSON
    }
