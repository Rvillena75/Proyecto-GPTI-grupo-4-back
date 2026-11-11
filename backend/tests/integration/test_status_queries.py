"""A targeted reduction removes an unused count read without weakening snapshot locking."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import event

from app.services.catalog import CatalogService
from app.services.inventory import InventoryService


def test_three_item_status_list_uses_sixteen_queries(clean_db):
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)
    with clean_db() as session:
        service = InventoryService(session)
        for name in ("A", "B", "C"):
            service.create_item(name, "kg", Decimal(1), "kg", "UTC", None, now)
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = clean_db.kw["bind"]
    event.listen(engine, "before_cursor_execute", record)
    try:
        with clean_db() as session:
            service = InventoryService(session)
            snapshots = [service.status(item.id, now) for item in service.items()]
            assert len(snapshots) == 3
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert len(statements) == 16
    assert not any("FROM counts" in statement for statement in statements)
    assert sum("FOR UPDATE" in statement for statement in statements) == 3


def test_catalog_insert_uses_item_before_supplier_to_avoid_fk_deadlock(clean_db):
    now = datetime(2026, 10, 18, 12, tzinfo=UTC)
    with clean_db() as session:
        item = InventoryService(session).create_item(
            "Lock order", "kg", Decimal(1), "kg", "UTC", None, now
        )
        supplier = CatalogService(session).create_supplier(
            {"name": "Lock supplier", "lead_time_days": 1, "is_active": True}, now
        )
    locks = []

    def record(connection, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement:
            locks.append(statement)

    engine = clean_db.kw["bind"]
    event.listen(engine, "before_cursor_execute", record)
    try:
        with clean_db() as session:
            CatalogService(session).create_product(
                supplier.id,
                {
                    "item_id": item.id,
                    "name_on_site": "Test pack",
                    "url": "https://example.test/test",
                    "pack_quantity": Decimal(1),
                    "min_packs": 1,
                    "is_active": True,
                },
            )
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert "FROM items" in locks[0] and "FROM suppliers" in locks[1]
