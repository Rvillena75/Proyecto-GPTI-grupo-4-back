"""Mass receipts and real opening deadlines; historical egg regressions stay separate."""

import copy
import json
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP
from decimal import Decimal as D
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.e2.dataset import export_dataset, generate
from app.e2.validation import validate_dataset, validate_files
from scraper.normalize import normalize
from scraper.pipeline import ADAPTERS, run_pipeline

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/e2"
CONFIG = FIXTURE / "config/dataset_v2.json"
EVIDENCE = FIXTURE / "evidencia/2026-10-07/asada"
REFERENCE = datetime.fromisoformat("2026-10-07T01:07:00-03:00")


@pytest.fixture(scope="module")
def dataset():
    return generate(CONFIG)


def test_mass_dataset_conserves_stock_with_only_expired_residual_waste(dataset, tmp_path):
    assert validate_dataset(dataset)["valid"]
    export_dataset(dataset, tmp_path)
    assert validate_files(tmp_path)["valid"]
    assert dataset["items"][0]["base_unit"] == "kg"
    assert all(D(row["balance_after"]) >= 0 for row in dataset["movements"])
    for row in dataset["demand"]:
        assert D(row["fulfilled_quantity"]) == min(
            D(row["demand_quantity"]), D(row["available_quantity"])
        )
        assert D(row["unmet_quantity"]) == D(row["demand_quantity"]) - D(row["fulfilled_quantity"])
    for receipt in dataset["receipts"]:
        assert D(receipt["quantity_received"]) == sum(D(x) for x in receipt["piece_quantities"])
        assert receipt["quantity_basis"] == "SYNTHETIC_MEASURED_WEIGHT"
    for lot in dataset["lots"]:
        used = sum(D(a["quantity"]) for a in dataset["allocations"] if a["lot_id"] == lot["id"])
        wasted = sum(D(w["quantity"]) for w in dataset["waste"] if w["lot_id"] == lot["id"])
        assert D(lot["quantity_received"]) == used + wasted + D(lot["remaining_quantity"])


def test_opening_is_first_actual_consumption_not_reception_and_keeps_clock(dataset):
    for lot in dataset["lots"]:
        uses = [a["occurred_at"] for a in dataset["allocations"] if a["lot_id"] == lot["id"]]
        if uses:
            assert lot["opened_at"] == min(uses)
            assert lot["opened_at"] > lot["received_at"]
            opened = datetime.fromisoformat(lot["opened_at"]).astimezone(
                ZoneInfo("America/Santiago")
            )
            assert datetime.fromisoformat(lot["expires_at"]) == min(
                datetime.fromisoformat(lot["closed_expires_at"]), opened + timedelta(days=5)
            )
            assert all(when < lot["expires_at"] for when in uses)
        else:
            assert lot["opened_at"] is None
            assert lot["expires_at"] == lot["closed_expires_at"]
    lots = {lot["id"]: lot for lot in dataset["lots"]}
    for waste in dataset["waste"]:
        assert waste["cause"] == "vencimiento"
        assert waste["occurred_at"] >= lots[waste["lot_id"]]["expires_at"]


def test_two_pieces_are_opened_independently_when_first_used(tmp_path):
    # Directed physical-piece regression; the canonical purchasing policy is never changed.
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg["opening_packs"] = 2
    cfg["price_snapshot"] = str((CONFIG.parent / cfg["price_snapshot"]).resolve())
    path = tmp_path / "two_pieces.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    data = generate(path, CONFIG.with_name("products.json"))
    opening = [lot for lot in data["lots"] if lot["receipt_id"] == "RCPT-E2-0001"]
    assert len(opening) == 2
    assert opening[0]["opened_at"] < opening[1]["opened_at"]
    assert opening[0]["expires_at"] < opening[1]["expires_at"]
    assert validate_dataset(data)["valid"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("opened_at", "2026-07-26T10:00:00-04:00"),
        ("closed_expires_at", "2026-07-28T00:00:00-04:00"),
        ("opened_shelf_life_days", 0),
        ("quantity_received", "2.5"),
    ],
)
def test_opening_and_weight_corruption_fails_validation(dataset, field, value):
    bad = copy.deepcopy(dataset)
    bad["lots"][0][field] = value
    assert not validate_dataset(bad)["valid"]


def test_prior_history_alone_determines_frozen_manual_purchase():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    history = json.loads((FIXTURE / "Historial_previo/consumo_previo.json").read_text("utf-8"))
    prior = sum(D(row["consumed_quantity"]) for row in history["rows"])
    estimate = cfg["purchase_plan_basis"]
    assert history["end_date"] < cfg["start_date"]
    assert history["seed"] != cfg["seed"]
    expected = max(1, int((prior / 7 * 5 / D("2.5")).to_integral_value(ROUND_HALF_UP)))
    assert estimate["pieces_per_order"] == expected
    assert all(plan["packs"] == expected for plan in cfg["purchase_plan"])
    assert [row["day_offset"] for row in cfg["purchase_plan"]] == list(range(0, 70, 5))


def test_closed_piece_price_and_fixed_slice_packs_normalize_from_public_snapshots(tmp_path):
    products = json.loads(CONFIG.with_name("products.json").read_text("utf-8"))
    expected = {1: D(13550), 2: D(20240), 3: D(20560)}
    for supplier in products["suppliers"]:
        body = (EVIDENCE / f"{supplier['evidence_stem']}.html").read_text("utf-8")
        supplier["item_id"] = products["item_id"]
        raw = ADAPTERS[supplier["adapter"]](supplier).parse(body, supplier["url"])
        quote = normalize(raw, supplier, REFERENCE, "frozen-public-evidence")
        assert D(quote["unit_price_clp"]) == expected[supplier["supplier_id"]]
        assert quote["base_unit"] == "kg"
        assert quote.get("is_variable_weight", False) == (supplier["supplier_id"] == 1)
    run = run_pipeline(
        CONFIG.with_name("products.json"),
        tmp_path,
        mode="replay",
        evidence=EVIDENCE,
        reference=REFERENCE,
        required=D("2.5"),
    )
    assert run["status"] == "OK" and len(run["quotes"]) == 3


def test_mass_dataset_uses_complete_28_days_for_original_safe_stock(dataset):
    assert D(dataset["summary"]["automatic_safe_stock_at_reference"]) == (
        D(dataset["summary"]["last_28_days_consumption"]) / 4
    )
    assert dataset["items"][0]["manual_safe_stock"] is None
    weekly = [row for row in dataset["counts"] if row["count_type"] == "WEEKLY_PHYSICAL"]
    assert len(weekly) == 10
