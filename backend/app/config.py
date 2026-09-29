"""Environment configuration without committed credentials."""

import os


def database_url() -> str:
    value = os.getenv("STOCKSMART_DATABASE_URL")
    if not value:
        raise RuntimeError("STOCKSMART_DATABASE_URL is required")
    if not value.startswith("postgresql+psycopg://"):
        raise RuntimeError("STOCKSMART_DATABASE_URL must use postgresql+psycopg")
    return value
