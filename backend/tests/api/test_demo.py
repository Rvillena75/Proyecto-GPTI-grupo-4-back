"""The explicit synthetic bootstrap is usable, has no fake prices and cannot target production."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from app import demo
from app.persistence.models import PriceQuote, Supplier, User

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/e2"


def test_demo_cli_serializes_replayed_dates_and_seeds_real_source_definitions(
    clean_db, monkeypatch, capsys
):
    monkeypatch.setattr(demo, "session_factory", lambda: clean_db)
    assert demo.main(["--fixture-root", str(FIXTURES)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] and report["no_e3_simulation"]
    assert report["receipts"] and report["dataset_sha256"]
    assert report["final_stock"] == "0.374000"
    with clean_db() as session:
        assert session.scalar(select(func.count(Supplier.id))) == 3
        assert session.scalar(select(func.count(PriceQuote.id))) == 0
        assert session.scalar(select(func.count(User.id))) == 0
    with pytest.raises(ValueError, match="empty dedicated"):
        demo.main(["--fixture-root", str(FIXTURES)])


def test_demo_never_targets_an_operational_database():
    factory = SimpleNamespace(
        kw={
            "bind": SimpleNamespace(url=make_url("postgresql+psycopg://test@127.0.0.1/operational"))
        }
    )
    with pytest.raises(ValueError, match="isolated"):
        demo.prepare(FIXTURES, factory)
