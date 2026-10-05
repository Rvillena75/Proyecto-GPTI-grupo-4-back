"""Environment configuration without committed credentials."""

import os


def database_url() -> str:
    value = os.getenv("STOCKSMART_DATABASE_URL")
    if not value:
        raise RuntimeError("STOCKSMART_DATABASE_URL is required")
    if not value.startswith("postgresql+psycopg://"):
        raise RuntimeError("STOCKSMART_DATABASE_URL must use postgresql+psycopg")
    return value


def cors_origins() -> list[str]:
    """Browser origins allowed to call the API, e.g. the frontend deployed on Vercel.

    Comma-separated in STOCKSMART_CORS_ORIGINS. Empty or unset allows no cross-origin calls.
    """
    value = os.getenv("STOCKSMART_CORS_ORIGINS", "")
    return [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
