from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_Session = None


def init_engine(url: str | None = None):
    global _engine, _Session
    settings = get_settings()
    url = url or settings.db_url
    if url.startswith("sqlite"):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(_engine, "connect")
        def _pragma(conn, _):  # WAL so the worker and API can share the file
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    else:
        _engine = create_engine(url, pool_pre_ping=True)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    from . import models  # noqa: F401  register tables
    Base.metadata.create_all(_engine)
    return _engine


def session():
    if _Session is None:
        init_engine()
    return _Session()
