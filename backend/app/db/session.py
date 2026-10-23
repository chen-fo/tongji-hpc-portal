from pathlib import Path

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    parsed = make_url(url)
    if parsed.drivername != "sqlite":
        raise ValueError("Phase 0/1 supports SQLite only")
    if parsed.database and parsed.database != ":memory:":
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 15})

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=15000")

    return engine


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_db(request: Request):
    with request.app.state.sessions() as session:
        yield session
