from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

db_path = settings.data_dir / "feynman.db"
engine = create_engine(
    f"sqlite:///{db_path.as_posix()}", connect_args={"timeout": 10, "check_same_thread": False}
)


@event.listens_for(engine, "connect")
def configure_sqlite(connection, _):
    with_cursor = connection.cursor()
    try:
        for statement in (
            "PRAGMA busy_timeout=10000",
            "PRAGMA journal_mode=WAL",
            "PRAGMA foreign_keys=ON",
        ):
            with_cursor.execute(statement)
    finally:
        with_cursor.close()


class Base(DeclarativeBase):
    pass


SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as db:
        yield db
