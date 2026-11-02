from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, create_engine, exists, insert, select
from sqlalchemy.orm import Session

from ..models import IngestedFile, Server


def canonicalize_path(path: str | Path, is_remote: bool = False) -> str:
    """Returns a canonical string representation of a path.
    For local files, resolves to an absolute path.
    For remote files, standardizes forward slashes.
    """
    if is_remote:
        return str(path)
    return str(Path(path).resolve())


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
    canonical_path = str(path)
    return bool(
        session.scalar(
            exists()
            .where(
                IngestedFile.server_id == server_id,
                IngestedFile.path == canonical_path,
            )
            .select()
        )
    )


def get_already_ingested_paths(
    session: Session, server_id: int, candidate_paths: Iterable[str]
) -> set[str]:
    """Retrieve the subset of candidate paths that have already been ingested,
    using batch queries rather than N individual queries.
    """
    path_list = list(candidate_paths)
    if not path_list:
        return set()

    found: set[str] = set()
    chunk_size = 5000
    for i in range(0, len(path_list), chunk_size):
        chunk = path_list[i : i + chunk_size]
        stmt = select(IngestedFile.path).where(
            IngestedFile.server_id == server_id,
            IngestedFile.path.in_(chunk),
        )
        found.update(session.scalars(stmt).all())
    return found


def record_ingested_file(
    session: Session, server_id: int, path: str | Path, commit: bool = True
) -> None:
    "Records an ingested file's path to the database."
    canonical_path = str(path)
    session.execute(
        insert(IngestedFile), [{"server_id": server_id, "path": canonical_path}]
    )
    if commit:
        session.commit()


@dataclass
class IngestionResult:
    path: str
    success: bool
    rows_count: int = 0
    extra_rows_count: int = 0
    duration: float = 0.0
    error: str | None = None


_worker_engine: Engine | None = None


def get_worker_engine(database_url: str) -> Engine:
    "Returns a process-local cached SQLAlchemy Engine instance."
    global _worker_engine
    if _worker_engine is None:
        _worker_engine = create_engine(database_url, pool_pre_ping=True)
    return _worker_engine
