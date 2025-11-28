from .db import engine
from .models import Base


def main() -> int:
    print("Lifewatch database seed tool")

    print("Creating database tables")
    Base.metadata.create_all(engine)
    print("Database created")

    return 0
