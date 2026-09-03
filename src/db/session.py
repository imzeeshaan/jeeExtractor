"""
Engine/session lifecycle. Only db/repositories.py and this module import
sqlalchemy directly for schema/session purposes — callers elsewhere use
repositories, never raw Session/Query objects.
"""
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from db.schema import Base


def get_engine(db_path, echo=False):
    # timeout=30: sqlite3's busy-wait window before raising "database is
    # locked" — the driver default (5s) was confirmed live to be too short
    # for a concurrent Streamlit rerun to wait out a write in progress.
    engine = create_engine(f"sqlite:///{db_path}", echo=echo, connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        # WAL allows concurrent readers while one writer is active (the
        # default DELETE journal mode locks the whole file on any write) —
        # the Review Studio's multiple Streamlit sessions/reruns read and
        # write the same file concurrently by design, not an edge case.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def init_db(engine):
    """Idempotent — creates any missing tables, never drops existing ones."""
    Base.metadata.create_all(engine)


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(session_factory):
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
