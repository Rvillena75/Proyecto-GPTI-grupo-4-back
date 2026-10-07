"""Database URL: the hosting provider's URL can be linked without editing it."""

import pytest

from app.config import database_url

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
