from lifewatch.db.models import Base
from lifewatch.db.engine import engine

Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)
