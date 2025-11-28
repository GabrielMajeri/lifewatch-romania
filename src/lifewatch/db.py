from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = "postgresql://dev:pwd@localhost:5432/lifewatch"

engine = create_engine(DATABASE_URL)


class Base(DeclarativeBase):
    pass
