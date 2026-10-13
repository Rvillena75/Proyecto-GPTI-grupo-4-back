from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import func, select

from app.domain.errors import RuleViolation
from app.e2.contracts import ApprovedOrder, PackSpec
from app.e2.receipts import ReceiptService
from app.persistence.models import Item, Movement
from app.services.inventory import InventoryService

START = datetime(2026, 10, 1, 12, tzinfo=UTC)  # Thu
RECEIVED = START + timedelta(days=2)  # Sat: calendar days, not weekdays.


def approved(item_id, *, status="APPROVED", identifier="oc-test") -> ApprovedOrder:
    return ApprovedOrder(
        id=identifier,
        code="OC-TEST",
        status=status,
        supplier_id=1,
        supplier_product_id=11,
        item_id=item_id,
        packs=2,
        pack=PackSpec(
            commercial_format="bandeja",
            content_quantity=D(1),
            content_unit="unidad",
            units_per_pack=30,
            base_unit="unidad",
        ),
        issued_at=START,
        approved_at=START,
        expected_at=RECEIVED,
        lead_time_days=2,
    )


def create_item(factory) -> int:
    with factory() as session:
        return (
            InventoryService(session)
            .create_item(
                "Huevos grandes", "unidad", D(10), "unidad", "America/Santiago", None, START
            )
            .id
        )


def receive(factory, order, receipt_id="rcpt-test", writer=None, **kwargs):
    with factory() as session:
        return ReceiptService(session, writer).receive(
            order, receipt_id, RECEIVED, date(2026, 10, 1), 30, RECEIVED, **kwargs
        )


def test_receipt_is_one_entry_with_lot_and_order_patch_and_idempotent_retry(clean_db) -> None:
    item_id = create_item(clean_db)
    order = approved(item_id)
    assert RECEIVED.weekday() == 5
    first = receive(clean_db, order)
    assert first.received_quantity == D(60)
    assert first.lot["produced_on"] == "2026-10-01"
    assert first.lot["expires_at"].startswith("2026-10-31")
    assert first.order_patch["receipt_movement_id"] == first.movement_id
    again = receive(clean_db, approved(item_id, status="RECEIVED"))
    assert again.idempotent_replay and again.movement_id == first.movement_id
    with clean_db() as session:
        assert session.get(Item, item_id).current_stock == D(70)
        assert (
            session.scalar(
                select(func.count()).select_from(Movement).where(Movement.kind == "ENTRY")
            )
            == 1
        )
    with pytest.raises(RuleViolation) as caught:
        receive(clean_db, order, "second-receipt")
    assert caught.value.code == "ORDER_ALREADY_RECEIVED"


def test_unapproved_partial_and_expired_receipt_do_not_change_inventory(clean_db) -> None:
    item_id = create_item(clean_db)
    with pytest.raises(RuleViolation) as caught:
        receive(clean_db, approved(item_id, status="REJECTED"))
    assert caught.value.code == "ORDER_NOT_APPROVED"
    with pytest.raises(RuleViolation) as caught:
        receive(clean_db, approved(item_id), received_packs=1)
    assert caught.value.code == "PARTIAL_RECEIPT_UNSUPPORTED"
    with clean_db() as session:
        with pytest.raises(RuleViolation) as caught:
            ReceiptService(session).receive(
                approved(item_id), "expired", RECEIVED, date(2026, 8, 1), 30, RECEIVED
            )
        assert caught.value.code == "EXPIRED_RECEIPT"
    with clean_db() as session:
        assert session.get(Item, item_id).current_stock == D(10)


def test_failure_updating_pedro_order_rolls_back_entry_and_stock(clean_db) -> None:
    item_id = create_item(clean_db)

    def writer(session, order_id, patch):
        assert session.in_transaction()
        assert order_id == "oc-test" and patch["status"] == "RECEIVED"
        assert session.get(Item, item_id).current_stock == D(70)
        raise RuntimeError("fixture order update failure")

    with pytest.raises(RuntimeError, match="fixture order update failure"):
        receive(clean_db, approved(item_id), writer=writer)
    with clean_db() as session:
        assert session.get(Item, item_id).current_stock == D(10)
        assert (
            session.scalar(
                select(func.count()).select_from(Movement).where(Movement.kind == "ENTRY")
            )
            == 0
        )


def test_concurrent_duplicate_receipts_enter_stock_once(clean_db) -> None:
    item_id = create_item(clean_db)
    order = approved(item_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: receive(clean_db, order), range(2)))
    assert len({r.movement_id for r in results}) == 1
    assert sorted(r.idempotent_replay for r in results) == [False, True]
    with clean_db() as session:
        assert session.get(Item, item_id).current_stock == D(70)


def test_receipt_id_cannot_be_reused_for_different_order(clean_db) -> None:
    item_id = create_item(clean_db)
    receive(clean_db, approved(item_id))
    with pytest.raises(RuleViolation) as caught:
        receive(clean_db, approved(item_id, identifier="other-order"))
    assert caught.value.code == "RECEIPT_CONFLICT"


def test_reversed_receipt_is_not_reported_as_valid_retry(clean_db) -> None:
    item_id = create_item(clean_db)
    result = receive(clean_db, approved(item_id))
    with clean_db() as session:
        InventoryService(session).reverse(result.movement_id, RECEIVED + timedelta(hours=1))
    with pytest.raises(RuleViolation) as caught:
        receive(clean_db, approved(item_id))
    assert caught.value.code == "RECEIPT_REVERSED"


def test_native_integer_order_id_is_compatible_with_pedro_numeric_id_style(clean_db) -> None:
    item_id = create_item(clean_db)
    result = receive(clean_db, approved(item_id, identifier=42))
    assert result.order_id == 42
    again = receive(clean_db, approved(item_id, status="RECEIVED", identifier=42))
    assert again.idempotent_replay and again.movement_id == result.movement_id


def test_retry_with_equivalent_timezone_representation_is_idempotent(clean_db) -> None:
    from zoneinfo import ZoneInfo

    item_id = create_item(clean_db)
    order = approved(item_id)
    first = receive(clean_db, order)
    zone = ZoneInfo("America/Santiago")
    local_order = order.model_copy(
        update={
            "issued_at": order.issued_at.astimezone(zone),
            "approved_at": order.approved_at.astimezone(zone),
            "expected_at": order.expected_at.astimezone(zone),
        }
    )
    with clean_db() as session:
        again = ReceiptService(session).receive(
            local_order, "rcpt-test", RECEIVED.astimezone(zone), date(2026, 10, 1), 30, RECEIVED
        )
    assert again.idempotent_replay and again.movement_id == first.movement_id


def test_rejected_order_cannot_reuse_an_existing_receipt(clean_db) -> None:
    item_id = create_item(clean_db)
    receive(clean_db, approved(item_id))
    with pytest.raises(RuleViolation) as caught:
        receive(clean_db, approved(item_id, status="REJECTED"))
    assert caught.value.code == "ORDER_NOT_APPROVED"
