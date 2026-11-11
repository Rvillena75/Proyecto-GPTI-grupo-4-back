"""Database connection and session setup."""

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import connection_timeout, database_url


@lru_cache(maxsize=1)
def session_factory() -> sessionmaker[Session]:
    engine = create_engine(
        database_url(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=2,
        pool_timeout=5,
        pool_recycle=300,
        connect_args={
            "connect_timeout": connection_timeout(),
            "options": "-c statement_timeout=10000 -c lock_timeout=5000",
        },
    )
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_session():  # type: ignore[no-untyped-def]
    with session_factory()() as session:
        yield session
