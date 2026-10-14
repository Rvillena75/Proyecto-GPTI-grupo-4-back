"""Directed test fixtures only: never canonical data or comparative E3 scenarios."""

import copy
import json
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path

from app.e2.dataset import generate
from app.e2.validation import validate_dataset

CONFIG = Path(__file__).resolve().parents[1] / "fixtures/e2/config/dataset_v2.json"


def directed_fixture(tmp_path, *, serving_g=100, weekdays=None, age=None, extra_receipt=False):
    """Isolate one mass balance with deterministic inputs in pytest's temporary directory."""
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg.update(
        days=28,
        opening_packs=1,
        purchase_plan=[],
        synthetic_piece_weight_g_range=[1000, 1000],
        recipes=[
            {
                "name": "directed_test_portion",
                "weekday_servings": weekdays if weekdays is not None else [1] * 7,
                "customer_jitter": 0,
                "quantity_per_serving": str(serving_g),
                "unit": "g",
            }
        ],
    )
    if age is not None:
        cfg["age_at_receipt_days"] = age
    if extra_receipt:
        cfg["purchase_plan"] = [
            {"day_offset": 0, "supplier_id": 1, "packs": 1, "reason": "directed_test_receipt"}
        ]
    cfg["price_snapshot"] = str((CONFIG.parent / cfg["price_snapshot"]).resolve())
    path = tmp_path / "directed_test_only.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    data = generate(path, CONFIG.with_name("products.json"))
    data["metadata"]["fixture_role"] = "DIRECTED_TEST_NOT_CANONICAL_NOT_E3"
    report = validate_dataset(data)
    assert report["valid"], report["errors"]
    return data


def test_directed_stockout_caps_consumption_records_lost_demand_and_exact_metric(tmp_path):
    data = directed_fixture(tmp_path, serving_g=600)
    first, second, empty = data["demand"][:3]
    assert D(first["fulfilled_quantity"]) == D("0.6")
    assert D(first["unmet_quantity"]) == 0
    assert D(second["available_quantity"]) == D("0.4")
    assert D(second["fulfilled_quantity"]) == D("0.4")
    assert D(second["unmet_quantity"]) == D("0.2") and second["stockout_day"]
    assert D(empty["fulfilled_quantity"]) == 0 and D(empty["unmet_quantity"]) == D("0.6")
    assert all(D(row["balance_after"]) >= 0 for row in data["movements"])
    assert all(D(row["closing_stock"]) >= 0 for row in data["daily_stock"])
    summary = data["summary"]
    assert D(summary["demand_quantity"]) == D("16.8")  # 28 days x 0.6 kg.
    assert D(summary["fulfilled_quantity"]) == 1
    assert D(summary["unmet_quantity"]) == D("15.8")
    assert D(summary["unmet_demand_percent"]) == D(3950) / 42  # 100 x 15.8 / 16.8.
    assert summary["stockout_days"] == 27
    assert D(summary["final_stock"]) == 0
    assert data["waste"] == []  # Lot fully consumed before expiration.


def test_exact_stock_exhaustion_is_not_unmet_demand(tmp_path):
    data = directed_fixture(tmp_path, serving_g=1000)
    first = data["demand"][0]
    assert D(first["demand_quantity"]) == D(first["available_quantity"]) == 1
    assert D(first["fulfilled_quantity"]) == 1 and D(first["unmet_quantity"]) == 0
    assert not first["stockout_day"]
    assert D(data["daily_stock"][0]["closing_stock"]) == 0
    assert data["waste"] == []


def test_partially_consumed_open_piece_expires_only_its_remaining_mass(tmp_path):
    data = directed_fixture(tmp_path)
    lot = data["lots"][0]
    used = sum(D(row["quantity"]) for row in data["allocations"])
    assert used == D("0.5")  # Five actual daily uses before the opening deadline.
    assert len(data["waste"]) == 1
    waste = data["waste"][0]
    assert D(waste["quantity"]) == D("0.5")
    assert waste["occurred_at"] == lot["expires_at"]
    assert D(lot["quantity_received"]) == used + D(waste["quantity"])
    assert D(lot["remaining_quantity"]) == 0
    assert waste["cause"] == "vencimiento"


def test_whole_unopened_expired_lot_is_discarded_without_fabricating_consumption(tmp_path):
    # Closed lot with four remaining days; first demand is on the weekend after expiry.
    # This is a boundary fixture, not a change to canonical age/life/menu parameters.
    data = directed_fixture(tmp_path, weekdays=[0, 0, 0, 0, 0, 1, 1], age=66)
    lot = data["lots"][0]
    assert lot["opened_at"] is None and data["allocations"] == []
    assert len(data["waste"]) == 1
    assert D(data["waste"][0]["quantity"]) == D(lot["quantity_received"]) == 1
    assert D(lot["remaining_quantity"]) == 0
    assert datetime.fromisoformat(data["waste"][0]["occurred_at"]) >= datetime.fromisoformat(
        lot["closed_expires_at"]
    )
    assert D(data["summary"]["final_stock"]) == 0


def test_fefo_prefers_available_open_piece_and_rejects_skipping_it(tmp_path):
    data = directed_fixture(tmp_path, extra_receipt=True)
    earlier, newer = data["lots"]
    allocation = next(a for a in data["allocations"] if a["demand_id"] == "DEM-2026-07-28")
    when = datetime.fromisoformat(allocation["occurred_at"])
    assert datetime.fromisoformat(newer["received_at"]) < when
    assert datetime.fromisoformat(earlier["expires_at"]) < datetime.fromisoformat(
        newer["closed_expires_at"]
    )
    assert allocation["lot_id"] == earlier["id"]
    bad = copy.deepcopy(data)
    wrong = next(a for a in bad["allocations"] if a["demand_id"] == "DEM-2026-07-28")
    wrong["lot_id"] = newer["id"]  # Already received, but later expiry: a FEFO violation.
    report = validate_dataset(bad)
    assert not report["valid"] and "Allocation violates FEFO" in report["errors"]
