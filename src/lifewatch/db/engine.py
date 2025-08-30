from sqlalchemy import create_engine

engine = create_engine("postgresql+psycopg://dev:pwd@localhost:5432/lifewatch")
