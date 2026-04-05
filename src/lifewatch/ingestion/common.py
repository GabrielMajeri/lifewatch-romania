from pathlib import Path
from sqlalchemy import Engine, exists, insert, select
from sqlalchemy.orm import Session

from ..models import IngestedFile, Server


def get_or_create_server(engine: Engine, host: str) -> int:
    """Returns the ID of the server entity corresponding to the given host,
    creating it if it doesn't exist.
    """
    with Session(engine) as session:
        stmt = select(Server.id).where(Server.hostname == host)

        server_id = session.execute(stmt).scalar_one_or_none()
        if server_id is not None:
            return server_id

        server_id = session.scalars(
            insert(Server).returning(Server.id), [{"hostname": host}]
        ).one()
        session.commit()

        return server_id


LOCAL_SERVER_NAME = "localhost"


def get_or_create_local_server(engine: Engine) -> int:
    """Returns the ID of the server entity corresponding to this host,
    creating it if it doesn't exist.
    """
    return get_or_create_server(engine, LOCAL_SERVER_NAME)


def check_file_already_ingested(
    session: Session, server_id: int, path: str | Path
) -> bool:
    "Checks whether the indicated file path, on the given server, has already been ingested / harvested / processed."
    return bool(
        session.scalar(
            exists()
            .where(IngestedFile.server_id == server_id, IngestedFile.path == str(path))
            .select()
        )
    )


def record_ingested_file(session: Session, server_id: int, path: str | Path) -> None:
    "Records an ingested file's path to the database."
    session.execute(insert(IngestedFile), [{"server_id": server_id, "path": str(path)}])
    session.commit()
