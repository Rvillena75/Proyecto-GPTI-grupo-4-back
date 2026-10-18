"""Database URL: the hosting provider's URL can be linked without editing it."""

import pytest

from app.config import connection_timeout, database_url

TARGET = "postgresql+psycopg://user:secret@db.internal:5432/stocksmart"


@pytest.mark.parametrize(
    "given",
    [
        TARGET,
        "postgresql://user:secret@db.internal:5432/stocksmart",
        "postgres://user:secret@db.internal:5432/stocksmart",
    ],
)
def test_provider_urls_use_the_psycopg_driver(monkeypatch, given: str) -> None:
    monkeypatch.setenv("STOCKSMART_DATABASE_URL", given)
    assert database_url() == TARGET


def test_other_databases_are_rejected(monkeypatch) -> None:
    monkeypatch.setenv("STOCKSMART_DATABASE_URL", "mysql://user:secret@db/stocksmart")
    with pytest.raises(RuntimeError, match="postgresql\\+psycopg"):
        database_url()


def test_missing_url_is_explained(monkeypatch) -> None:
    monkeypatch.delenv("STOCKSMART_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="required"):
        database_url()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg:///missing_host",
        "postgresql+psycopg://host",
        "postgresql+psycopg://user@host:wrong/db",
    ],
)
def test_incomplete_urls_never_fall_back_to_local_defaults(monkeypatch, url):
    monkeypatch.setenv("STOCKSMART_DATABASE_URL", url)
    with pytest.raises(RuntimeError, match="explicit host"):
        database_url()


@pytest.mark.parametrize("value", ["0", "-1", "11", "not-a-number"])
def test_invalid_timeout_cannot_disable_bounds(monkeypatch, value):
    monkeypatch.setenv("STOCKSMART_DB_CONNECT_TIMEOUT", value)
    with pytest.raises(RuntimeError, match="between 1 and 10"):
        connection_timeout()
