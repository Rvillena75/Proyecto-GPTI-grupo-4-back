"""Environment configuration without committed credentials."""

import os


def database_url() -> str:
    """SQLAlchemy URL for PostgreSQL with the psycopg 3 driver.

    Hosting providers such as Render hand out ``postgres://`` or ``postgresql://`` URLs;
    those are rewritten to ``postgresql+psycopg://`` so the variable can be linked as is.
    """
    value = os.getenv("STOCKSMART_DATABASE_URL")
    if not value:
        raise RuntimeError("STOCKSMART_DATABASE_URL is required")
    for scheme in ("postgres://", "postgresql://"):
        if value.startswith(scheme):
            value = "postgresql+psycopg://" + value.removeprefix(scheme)
    if not value.startswith("postgresql+psycopg://"):
        raise RuntimeError("STOCKSMART_DATABASE_URL must use postgresql+psycopg")
    return value


def cors_origins() -> list[str]:
    """Browser origins allowed to call the API, e.g. the frontend deployed on Vercel.

    Comma-separated in STOCKSMART_CORS_ORIGINS. Empty or unset allows no cross-origin calls.
    """
    value = os.getenv("STOCKSMART_CORS_ORIGINS", "")
    return [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
