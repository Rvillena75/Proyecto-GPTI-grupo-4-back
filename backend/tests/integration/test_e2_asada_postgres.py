from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.domain.errors import RuleViolation
from app.e2.contracts import ApprovedOrder, PackSpec
from app.e2.dataset import generate
from app.e2.receipts import ReceiptService
from app.e2.replay import replay_inventory
from app.persistence.models import Item, Movement
from app.services.inventory import InventoryService

START = datetime(2026, 10, 1, 12, tzinfo=UTC)
RECEIVED = START + timedelta(days=1)


def order_and_item(factory, *, packs=1, variable=True):
    with factory() as session:
        item = InventoryService(session).create_item(
            "Pechuga asada", "kg", D("0.3"), "kg", "America/Santiago", None, START
        )
    return ApprovedOrder(
        id="oc-asada",
        code="OC-ASADA",
        status="APPROVED",
        supplier_id=1,
        supplier_product_id=201,
        item_id=item.id,
        packs=packs,
        pack=PackSpec(
            commercial_format="pieza",
            content_quantity=D(2500),
            content_unit="g",
            units_per_pack=1,
            base_unit="kg",
            is_variable_weight=variable,
        ),
        issued_at=START,
        approved_at=START,
        expected_at=RECEIVED,
        lead_time_days=1,
    )


def receive(factory, order, *, writer=None, **kwargs):
    with factory() as session:
        return ReceiptService(session, writer).receive(
            order, "rcpt-asada", RECEIVED, date(2026, 9, 30), 70, RECEIVED, **kwargs
        )


def test_variable_weight_enters_actual_mass_and_retry_remains_idempotent(clean_db):
    order = order_and_item(clean_db)
    first = receive(clean_db, order, actual_received_quantity=D("2.473"), opened_shelf_life_days=5)
    again = receive(
        clean_db, order, actual_received_quantity=D("2.473000"), opened_shelf_life_days=5
    )
    assert again.idempotent_replay and again.movement_id == first.movement_id
    assert first.received_quantity == D("2.473")
    assert first.lot["nominal_quantity"] == "2.500000"
    assert first.lot["piece_quantities"] == ["2.473000"]
    assert first.order_patch["received_quantity"] == D("2.473")
    assert "opened_at" not in first.lot  # Receipt never opens a piece.
    with clean_db() as session:
        assert session.get(Item, order.item_id).current_stock == D("2.773")
        assert (
            session.scalar(
                select(func.count()).select_from(Movement).where(Movement.kind == "ENTRY")
            )
            == 1
        )


@pytest.mark.parametrize("weight", [None, D(0), D(-1), D("NaN"), D("2.5000001")])
def test_invalid_or_missing_actual_weight_does_not_enter_stock(clean_db, weight):
    order = order_and_item(clean_db)
    with pytest.raises(RuleViolation):
        receive(clean_db, order, actual_received_quantity=weight)
    with clean_db() as session:
        assert session.get(Item, order.item_id).current_stock == D("0.3")


def test_variable_multiple_pieces_require_individual_weights_and_preserve_ids(clean_db):
    order = order_and_item(clean_db, packs=2)
    with pytest.raises(RuleViolation) as caught:
        receive(clean_db, order, actual_received_quantity=D("5.01"), opened_shelf_life_days=5)
    assert caught.value.code == "PIECE_WEIGHTS_REQUIRED"
    result = receive(
        clean_db,
        order,
        actual_received_quantity=D("5.01"),
        opened_shelf_life_days=5,
        piece_quantities=[D("2.48"), D("2.53")],
    )
    assert result.lot["piece_lot_ids"] == ["LOT-rcpt-asada-P01", "LOT-rcpt-asada-P02"]
    assert result.received_quantity == D("5.01")


def test_fixed_net_formats_cannot_be_silently_reweighed(clean_db):
    order = order_and_item(clean_db, packs=2, variable=False)
    with pytest.raises(RuleViolation) as caught:
        receive(clean_db, order, actual_received_quantity=D("4.9"))
    assert caught.value.code == "FIXED_FORMAT_MISMATCH"
    with pytest.raises(RuleViolation) as caught:
        receive(clean_db, order, piece_quantities=[D(2), D(3)])
    assert caught.value.code == "FIXED_FORMAT_MISMATCH"


def test_variable_receipt_rollback_on_pedro_writer_error(clean_db):
    order = order_and_item(clean_db)

    def writer(session, order_id, patch):
        assert session.in_transaction() and patch["received_quantity"] == D("2.48")
        raise RuntimeError("order writer failed")

    with pytest.raises(RuntimeError, match="order writer failed"):
        receive(clean_db, order, writer=writer, actual_received_quantity=D("2.48"))
    with clean_db() as session:
        assert session.get(Item, order.item_id).current_stock == D("0.3")


def test_final_asada_dataset_replays_through_unchanged_postgres_core(clean_db):
    config = Path(__file__).resolve().parents[1] / "fixtures/e2/config/dataset_v2.json"
    data = generate(config)
    report = replay_inventory(data, clean_db)
    assert report["valid"]
    assert report["movements_replayed"] == len(data["movements"])
    assert report["receipt_entries"] == len(data["receipts"]) - 1
    assert D(report["final_stock"]) == D(data["summary"]["final_stock"])
    assert D(report["expiry_merma"]) == D(data["summary"]["waste_quantity"])
