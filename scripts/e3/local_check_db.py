"""PostgreSQL utilities for reproducible local E3 evidence."""

import os
import re
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND = Path(__file__).resolve().parents[2] / "backend"
LOCAL = "host=127.0.0.1 port=55439 user=stocksmart_e3 connect_timeout=5"


def local_database(name: str, *, reuse: bool = False):
    if not re.fullmatch(r"stocksmart_e3_[a-z0-9_]{1,40}_test", name):
        raise ValueError("Use a dedicated stocksmart_e3_*_test database name")
    with psycopg.connect(f"{LOCAL} dbname=postgres", autocommit=True) as admin:
        exists = admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone()
        if exists and not reuse:
            raise ValueError(f"{name} exists. Choose a new run key to preserve evidence.")
        if not exists:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = f"postgresql+psycopg://stocksmart_e3@127.0.0.1:55439/{name}"
    os.environ["STOCKSMART_DATABASE_URL"] = url
    os.environ["STOCKSMART_DAILY_SCRAPE_ENABLED"] = "false"
    command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")
    return sessionmaker(bind=create_engine(url, pool_pre_ping=True), expire_on_commit=False)
