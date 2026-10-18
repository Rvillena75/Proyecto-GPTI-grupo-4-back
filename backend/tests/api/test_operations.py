"""Worker coordination, safe migration upgrade and private backup/restore on real PostgreSQL."""

import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_procurement import NOW, EvidenceTransport, catalog

from app import backups
from app.persistence.models import Count, Item, Supplier, User
from app.services.auth import authenticate_token, issue_token, verify_password
from app.services.inventory import InventoryService
from scraper.coordination import coordinated_worker
from scraper.store import WorkerBusyError


@contextmanager
def isolated_database(factory):
    source = factory.kw["bind"]
    assert source.url.database.endswith("_test")
    name = "stocksmart_migration_" + uuid4().hex[:12] + "_test"
    with source.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    target_url = source.url.set(database=name).render_as_string(hide_password=False)
    engine = create_engine(target_url)
    try:
        yield engine, target_url
    finally:
        engine.dispose()
        assert name.startswith("stocksmart_migration_") and name.endswith("_test")
        with source.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))


def test_distributed_worker_ignores_independent_filesystems_and_releases(clean_db, tmp_path):
    env = {
        **os.environ,
        "STOCKSMART_DATABASE_URL": clean_db.kw["bind"].url.render_as_string(hide_password=False),
        "STOCKSMART_PRICE_EVIDENCE_DIR": str(tmp_path / "other-service"),
    }
    code = (
        "from scraper.runtime import price_transport; from scraper.store import WorkerBusyError\n"
        "try:\n with price_transport(): pass\nexcept WorkerBusyError: raise SystemExit(3)\n"
    )
    with coordinated_worker(clean_db):
        process = subprocess.run(
            [sys.executable, "-c", code], env=env, capture_output=True, timeout=15
        )
        assert process.returncode == 3, process.stderr.decode(errors="replace")
    with coordinated_worker(clean_db):
        pass


def test_worker_crash_releases_postgresql_lock(clean_db, tmp_path):
    ready = tmp_path / "ready"
    env = {
        **os.environ,
        "STOCKSMART_DATABASE_URL": clean_db.kw["bind"].url.render_as_string(hide_password=False),
    }
    script = tmp_path / "hold.py"
    script.write_text(
        "from scraper.coordination import coordinated_worker\n"
        "from pathlib import Path\nimport time\n"
        f"with coordinated_worker():\n Path({str(ready)!r}).write_text('ready')\n"
        " time.sleep(30)\n"
    )
    process = subprocess.Popen(
        [sys.executable, str(script)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            assert process.poll() is None
            time.sleep(0.05)
        assert ready.exists()
        with pytest.raises(WorkerBusyError), coordinated_worker(clean_db):
            pass
    finally:
        process.terminate()
        process.wait(timeout=10)
    deadline = time.monotonic() + 5
    while True:
        try:
            with coordinated_worker(clean_db):
                break
        except WorkerBusyError:
            assert time.monotonic() < deadline, "Database did not release the dead worker lock"
            time.sleep(0.05)


def test_cross_service_pacing_and_abandoned_runs(clean_db):
    with clean_db() as session, session.begin():
        supplier = Supplier(
            name="Coordination test",
            lead_time_days=0,
            is_active=True,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(supplier)
        session.flush()
        session.execute(
            text(
                "INSERT INTO scrape_runs(supplier_id,started_at,status) "
                "VALUES (:id,now(),'RUNNING')"
            ),
            {"id": supplier.id},
        )
    host = "www.jumbo.cl"
    with coordinated_worker(clean_db) as first:
        first.before_request(host, 2)
        first.after_request(host)
        previous = first.connection.scalar(
            text("SELECT last_finished_at FROM price_host_clocks WHERE host=:h"), {"h": host}
        )
    with coordinated_worker(clean_db) as second:
        second.before_request(host, 2)
        actual = second.connection.scalar(text("SELECT clock_timestamp()"))
        assert (actual - previous).total_seconds() >= 2
        second.after_request(host)
        status = second.connection.execute(
            text("SELECT status,finished_at,message FROM scrape_runs")
        ).one()
        assert status.status == "ERROR" and status.finished_at is not None
        assert "WORKER_INTERRUPTED" in status.message


@pytest.mark.parametrize("fail,exit_code", [(None, 0), ("central", 2), ("all", 1)])
def test_worker_codes_are_observable(api_client, clean_db, monkeypatch, fail, exit_code):
    from scraper import postgres

    client, _, _, _ = catalog(api_client)

    class Transport(EvidenceTransport):
        def fetch_product(self, url, adapter):
            if fail == "all":
                raise OSError("Simulated evidence write failure")
            return super().fetch_product(url, adapter)

    @contextmanager
    def transport():
        yield Transport(fail=fail)

    monkeypatch.setattr(postgres, "session_factory", lambda: clean_db)
    monkeypatch.setattr(postgres, "price_transport", transport)
    assert postgres.main(["--all-active"]) == exit_code


@pytest.mark.parametrize("corrupt", [False, True])
def test_upgrade_preserves_populated_0004_and_fails_on_partial_receipt(
    clean_db, monkeypatch, corrupt
):
    with isolated_database(clean_db) as (engine, url):
        monkeypatch.setenv("STOCKSMART_DATABASE_URL", url)
        config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        command.upgrade(config, "0004_scrape_config")
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users(email,password_hash,name,role,is_active,created_at) "
                    "VALUES ('legacy@example.test','legacy-hash-test','Legacy','ADMIN',true,now())"
                )
            )
        with Session(engine, expire_on_commit=False) as session:
            service = InventoryService(session, 1)
            item = service.create_item("Legacy kg", "kg", Decimal(10), "kg", "UTC", None, NOW)
            service.record_count(item.id, Decimal(9), "kg", NOW)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO suppliers(name,lead_time_days,is_active,created_at,updated_at) "
                    "VALUES ('Legacy supplier',2,true,now(),now())"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO supplier_products(supplier_id,item_id,name_on_site,url,"
                    "pack_quantity,min_packs,is_active) "
                    "VALUES (1,1,'Legacy pack','https://example.test/product',1,1,true)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO scrape_runs(supplier_id,started_at,finished_at,status) "
                    "VALUES (1,now(),now(),'OK')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO price_quotes(supplier_product_id,run_id,pack_price_clp,"
                    "unit_price_clp,available,observed_at,valid_until) "
                    "VALUES (1,1,1000,1000,true,now(),now()+interval '7 days')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO suggestions(item_id,status,required_qty,in_transit_qty,"
                    "supplier_product_id,packs,purchased_qty,total_clp,inputs_snapshot,"
                    "created_at,created_by) VALUES (1,'APPROVED',1,0,1,1,1,1000,'{}',now(),1)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO orders(suggestion_id,code,status,decided_by,decided_at,"
                    "received_at) "
                    "VALUES (1,'OC-LEGACY','APPROVED',1,now(),"
                    + ("now()" if corrupt else "NULL")
                    + ")"
                )
            )
        columns = {
            table: [c["name"] for c in inspect(engine).get_columns(table)]
            for table in (
                "users",
                "items",
                "observations",
                "movements",
                "counts",
                "suppliers",
                "supplier_products",
                "scrape_runs",
                "price_quotes",
                "suggestions",
                "orders",
            )
        }

        def snapshot():
            with engine.connect() as connection:
                return {
                    table: list(
                        connection.execute(
                            text("SELECT " + ",".join(names) + f" FROM {table} ORDER BY id")
                        )
                    )
                    for table, names in columns.items()
                }

        before = snapshot()
        if corrupt:
            with pytest.raises(IntegrityError):
                command.upgrade(config, "head")
            assert snapshot() == before
            with engine.begin() as connection:
                connection.execute(text("UPDATE orders SET received_at=NULL"))
            before = snapshot()
        command.upgrade(config, "head")
        assert snapshot() == before
        command.check(config)


def test_seven_daily_backups_restore_identities_and_inventory(
    api_client, clean_db, tmp_path, monkeypatch, test_password_hash
):
    client, _ = api_client
    client.post(
        "/items",
        json={
            "name": "Restore sample",
            "base_unit": "kg",
            "initial_quantity": "10",
            "initial_unit": "kg",
        },
    )
    client.post("/counts", json={"item_id": 1, "physical_quantity": "8", "unit": "kg"})
    with clean_db() as session, session.begin():
        session.add(
            User(
                email="restored-warehouse@example.test",
                name="Restored warehouse",
                role="WAREHOUSE",
                is_active=True,
                password_hash=test_password_hash,
                created_at=NOW,
            )
        )
    if os.name == "nt":
        monkeypatch.setenv("STOCKSMART_PG_BIN", "C:/Program Files/PostgreSQL/18/bin")
    source_url = clean_db.kw["bind"].url.render_as_string(hide_password=False)
    private = tmp_path / "private"
    for day in range(9):
        destination = backups.daily_local(source_url, private, NOW + timedelta(days=day))
    assert len(list(private.glob("stocksmart-backup-*"))) == 7
    with isolated_database(clean_db) as (target, url):
        backups.restore(url, destination)
        with Session(target) as session:
            assert session.get(Item, 1).current_stock == 8
            assert session.scalar(select(Count)).shortage == 2
            user = session.get(User, 1)
            assert verify_password("Test password only 2026!", user.password_hash)
            assert authenticate_token(session, issue_token(user)).role == "ADMIN"
            warehouse = session.scalar(select(User).where(User.role == "WAREHOUSE"))
            assert verify_password("Test password only 2026!", warehouse.password_hash)
            assert authenticate_token(session, issue_token(warehouse)).role == "WAREHOUSE"
        with pytest.raises(ValueError, match="empty"):
            backups.restore(url, destination)
    original = len(list(private.glob("stocksmart-backup-*")))
    monkeypatch.setattr(
        backups, "archive", lambda *a: (_ for _ in ()).throw(RuntimeError("dump failed"))
    )
    with pytest.raises(RuntimeError):
        backups.daily_local(source_url, private, NOW + timedelta(days=10))
    assert len(list(private.glob("stocksmart-backup-*"))) == original


def test_restore_never_accepts_operational_database(tmp_path):
    with pytest.raises(ValueError, match="isolated"):
        backups.restore("postgresql+psycopg://test@127.0.0.1/production", tmp_path)
