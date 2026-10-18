"""Replay E2 data into existing PostgreSQL inventory, not E3 policy simulation."""

import argparse
import json
import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.api.schemas import CountCreate, ItemCreate, MovementCreate
from app.domain.replenishment import (
    SupplierLeadTime,
    assess_replenishment,
    maximum_active_lead_time,
)
from app.e2.contracts import ApprovedOrder
from app.e2.dataset import json_text, write_json
from app.e2.receipts import ReceiptService
from app.e2.validation import validate_dataset
from app.persistence.models import Item, Movement
from app.services.inventory import InventoryService

D = Decimal


def replay_inventory(data: dict, factory) -> dict:
    validation = validate_dataset(data)
    if not validation["valid"]:
        raise ValueError(json_text(validation))
    with factory() as session:
        if session.scalar(select(func.count()).select_from(Item)):
            raise ValueError(
                "Replay requires an empty dedicated test database; no data will be deleted"
            )
    source_item = data["items"][0]
    payload = ItemCreate(
        name=source_item["name"],
        base_unit=source_item["base_unit"],
        initial_quantity=source_item["initial_quantity"],
        initial_unit=source_item["base_unit"],
        timezone=source_item["timezone"],
    )
    with factory() as session:
        item = InventoryService(session).create_item(
            payload.name,
            payload.base_unit,
            payload.initial_quantity,
            payload.initial_unit,
            payload.timezone,
            payload.manual_safe_stock,
            datetime.fromisoformat(source_item["observation_started_at"]),
        )
        item_id = item.id
    counts = {row["adjustment_movement_id"]: row for row in data["counts"]}
    receipts = {row["id"]: row for row in data["receipts"]}
    orders = {row["id"]: row for row in data["orders"]}
    lots = {row["id"]: row for row in data["lots"]}
    mapping = {}
    receipt_results = []
    for row in sorted(
        data["movements"], key=lambda r: (datetime.fromisoformat(r["occurred_at"]), r["id"])
    ):
        if row["kind"] == "INITIAL":
            continue
        at = datetime.fromisoformat(row["occurred_at"])
        with factory() as session:
            service = InventoryService(session)
            if row["kind"] == "ENTRY":
                receipt = receipts[row["receipt_id"]]
                source_order = orders[receipt["order_id"]]
                order = ApprovedOrder.model_validate(
                    {
                        **{
                            key: source_order[key]
                            for key in (
                                "id",
                                "code",
                                "supplier_id",
                                "supplier_product_id",
                                "packs",
                                "pack",
                                "issued_at",
                                "approved_at",
                                "expected_at",
                                "lead_time_days",
                            )
                        },
                        "status": "APPROVED",
                        "item_id": item_id,
                    }
                )
                lot = lots[receipt["lot_id"]]
                extra = {}
                if receipt.get("is_variable_weight"):
                    extra["actual_received_quantity"] = D(receipt["quantity_received"])
                if "opened_shelf_life_days" in lot:
                    extra["opened_shelf_life_days"] = lot["opened_shelf_life_days"]
                    extra["piece_quantities"] = [D(x) for x in receipt["piece_quantities"]]
                result = ReceiptService(session).receive(
                    order,
                    receipt["id"],
                    at,
                    date.fromisoformat(lot["produced_on"]),
                    lot["shelf_life_days"],
                    at,
                    **extra,
                )
                expected_ids = receipt.get("lot_ids", [receipt["lot_id"]])
                actual_ids = result.lot.get("piece_lot_ids", [result.lot["lot_id"]])
                if actual_ids != expected_ids or result.received_quantity != D(
                    receipt["quantity_received"]
                ):
                    raise ValueError("Dataset and reception lot identifiers differ")
                mapping[row["id"]] = result.movement_id
                receipt_results.append(
                    {
                        "receipt_id": result.receipt_id,
                        "order_id": result.order_id,
                        "movement_id": result.movement_id,
                        "lot_id": result.lot["lot_id"],
                        "order_patch": result.order_patch,
                    }
                )
            elif row["kind"] == "EXIT":
                movement_input = MovementCreate(
                    item_id=item_id,
                    kind="EXIT",
                    quantity=-D(row["delta"]),
                    unit=source_item["base_unit"],
                    occurred_at=at,
                )
                movement = service.add_movement(
                    movement_input.item_id,
                    movement_input.kind,
                    movement_input.quantity,
                    movement_input.unit,
                    at,
                    movement_input.occurred_at,
                )
                mapping[row["id"]] = movement.id
            elif row["kind"] == "COUNT_ADJUSTMENT":
                source_count = counts[row["id"]]
                count_input = CountCreate(
                    item_id=item_id,
                    physical_quantity=source_count["physical_stock"],
                    unit=source_item["base_unit"],
                    cause=source_count["cause"],
                )
                count = service.record_count(
                    count_input.item_id,
                    count_input.physical_quantity,
                    count_input.unit,
                    at,
                    count_input.cause,
                )
                if count.theoretical_before != D(source_count["theoretical_before"]):
                    raise ValueError(
                        "Replay physical count differs from its immutable theoretical snapshot"
                    )
                mapping[row["id"]] = count.adjustment_movement_id
        with factory() as session:
            if session.get(Item, item_id).current_stock != D(row["balance_after"]):
                raise ValueError(f"PostgreSQL balance diverges at source movement {row['id']}")
    reference = datetime.fromisoformat(data["metadata"]["reference_at"])
    with factory() as session:
        status = InventoryService(session).status(item_id, reference)
        final_stock = status["current_stock"]
        consumption = status["consumption"]
        movement_count = session.scalar(select(func.count()).select_from(Movement))
    with factory() as session:
        rate = InventoryService(session).shortage_rate(
            item_id, datetime.fromisoformat(source_item["observation_started_at"]), reference
        )
    lead = maximum_active_lead_time(
        [
            SupplierLeadTime(s["supplier_id"], s["lead_time_days"], s["is_active"])
            for s in data["suppliers"]
        ]
    )
    assessment = assess_replenishment(
        final_stock, None, consumption, reference, lead_time_days=lead
    )
    expected = data["summary"]
    if (
        final_stock != D(expected["final_stock"])
        or consumption.observed_quantity != D(expected["last_28_days_consumption"])
        or assessment.safe_stock != D(expected["automatic_safe_stock_at_reference"])
        or rate["shortage"] != D(expected["waste_quantity"])
    ):
        raise ValueError(
            "PostgreSQL consumption/stock/safe stock/expiry merma differs from Dataset v2"
        )
    return {
        "valid": True,
        "item_id": item_id,
        "movements_replayed": movement_count,
        "receipt_entries": len(receipt_results),
        "receipts": receipt_results,
        "final_stock": str(final_stock),
        "last_28_days_consumption": str(consumption.observed_quantity),
        "automatic_safe_stock": str(assessment.safe_stock),
        "expiry_merma": str(rate["shortage"]),
        "p3_available": str(rate["available"]),
        "p3_rate_percent": str(rate["rate_percent"]),
        "active_max_calendar_lead_time": lead,
        "order_repository_integration": "DATASET_TEST_RECEIPT_PORT; OPERATIONAL_HTTP_FLOW_SEPARATE",
        "existing_http_alert_endpoints": "API_USES_ACTIVE_CATALOG; REPLAY_DOES_NOT_SEED_CATALOG",
        "no_e3_simulation": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--database-url", default=os.getenv("STOCKSMART_TEST_DATABASE_URL"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("Set STOCKSMART_TEST_DATABASE_URL to an empty PostgreSQL *_test database")
    parsed = make_url(args.database_url)
    if parsed.get_backend_name() != "postgresql" or not (parsed.database or "").endswith("_test"):
        raise SystemExit("Only a dedicated PostgreSQL database ending in _test is allowed")
    os.environ["STOCKSMART_DATABASE_URL"] = args.database_url
    backend = Path(__file__).resolve().parents[2]
    config = Config(str(backend / "alembic.ini"))
    command.upgrade(config, "head")
    engine = create_engine(args.database_url)
    try:
        data = json.loads((args.dataset / "dataset.json").read_text(encoding="utf-8"))
        report = replay_inventory(data, sessionmaker(bind=engine, expire_on_commit=False))
        write_json(args.out, report)
        print(json_text(report))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
