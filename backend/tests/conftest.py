"""Dedicated PostgreSQL database fixtures; no transactional test uses SQLite."""

import os
import secrets
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.api.auth import get_auth_session
from app.api.routes import get_now
from app.main import app
from app.persistence.database import get_session
from app.persistence.models import User
from app.services.auth import hash_password, issue_token


@pytest.fixture(scope="session")
def db_factory() -> Iterator[sessionmaker[Session]]:
    url = os.getenv("STOCKSMART_TEST_DATABASE_URL")
    if not url:
        pytest.fail("Set STOCKSMART_TEST_DATABASE_URL to a dedicated PostgreSQL *_test database")
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or not (parsed.database or "").endswith("_test"):
        pytest.fail("Tests require a dedicated PostgreSQL database with a name ending in _test")
    previous = os.environ.get("STOCKSMART_DATABASE_URL")
    os.environ["STOCKSMART_DATABASE_URL"] = url
    engine = create_engine(url, pool_pre_ping=True)
    # Rebuild only the explicitly named test database, proving migrations work from empty.
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()
    if previous is None:
        os.environ.pop("STOCKSMART_DATABASE_URL", None)
    else:
        os.environ["STOCKSMART_DATABASE_URL"] = previous


@pytest.fixture
def clean_db(db_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    with db_factory.kw["bind"].begin() as connection:
        connection.execute(
            text(
                "TRUNCATE orders, suggestions, price_quotes, scrape_runs, supplier_products, "
                "suppliers, counts, movements, observations, items, users, login_throttles, "
                "price_host_clocks "
                "RESTART IDENTITY CASCADE"
            )
        )
    return db_factory


@pytest.fixture
def api_client(
    clean_db: sessionmaker[Session],
    monkeypatch,
    test_password_hash,
) -> Iterator[tuple[TestClient, Callable[[datetime], None]]]:
    current = {"at": datetime(2026, 9, 28, 12, tzinfo=UTC)}
    monkeypatch.setenv("STOCKSMART_JWT_SECRET", secrets.token_urlsafe(48))
    with clean_db() as session, session.begin():
        admin = User(
            email="admin@example.test",
            password_hash=test_password_hash,
            name="Test Admin",
            role="ADMIN",
            is_active=True,
            created_at=datetime.now(UTC),
        )
        session.add(admin)
        session.flush()
    token = issue_token(admin)

    def session_override() -> Iterator[Session]:
        with clean_db() as session:
            yield session

    def now_override() -> datetime:
        return current["at"]

    def set_time(value: datetime) -> None:
        current["at"] = value

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_auth_session] = session_override
    app.dependency_overrides[get_now] = now_override
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + token
        yield client, set_time
    app.dependency_overrides.clear()


@pytest.fixture(scope="session")
def test_password_hash():
    return hash_password("Test password only 2026!")
