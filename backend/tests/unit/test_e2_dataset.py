import copy
import json
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.e2.dataset import export_dataset, generate
from app.e2.validation import validate_dataset, validate_files

CONFIG = Path(__file__).resolve().parents[1] / "fixtures/e2_huevos/config/dataset_v2.json"


@pytest.fixture(scope="module")
def dataset() -> dict:
    return generate(CONFIG)


def test_reproducible_csv_and_json_including_dst(dataset, tmp_path) -> None:
    assert generate(CONFIG) == dataset
    first = export_dataset(dataset, tmp_path / "first")
    second = export_dataset(generate(CONFIG), tmp_path / "second")
    assert first == second
    for name in first["files"]:
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()
    assert validate_files(tmp_path / "first")["valid"]
    dates = [row["date"] for row in dataset["demand"]]
    assert "2026-09-06" in dates  # DST change remains one observed calendar day.
    assert len(dates) == len(set(dates)) == 70


def test_actual_fixture_records_outcomes_without_forcing_expiry(dataset) -> None:
    assert validate_dataset(dataset)["valid"]
    assert all(D(row["closing_stock"]) >= 0 for row in dataset["daily_stock"])
    assert all(D(row["balance_after"]) >= 0 for row in dataset["movements"])
    assert any(D(row["unmet_quantity"]) > 0 for row in dataset["demand"])
    for row in dataset["demand"]:
        assert D(row["fulfilled_quantity"]) == min(
            D(row["demand_quantity"]), D(row["available_quantity"])
        )
        assert D(row["unmet_quantity"]) == D(row["demand_quantity"]) - D(row["fulfilled_quantity"])
    assert {lot["shelf_life_days"] for lot in dataset["lots"]} == {30}
    assert all(row["cause"] == "vencimiento" for row in dataset["waste"])


def test_last_28_days_have_independent_complete_day_sum(dataset) -> None:
    reference = date(2026, 10, 5)
    expected = sum(
        D(row["fulfilled_quantity"])
        for row in dataset["demand"]
        if reference - timedelta(days=28) <= date.fromisoformat(row["date"]) < reference
    )
    assert D(dataset["summary"]["last_28_days_consumption"]) == expected
    assert D(dataset["summary"]["automatic_safe_stock_at_reference"]) == expected / 4
    assert dataset["items"][0]["manual_safe_stock"] is None
    weekly = [row for row in dataset["counts"] if row["count_type"] == "WEEKLY_PHYSICAL"]
    assert len(weekly) == 10
    dates = [date.fromisoformat(row["occurred_at"][:10]) for row in weekly]
    assert all((b - a).days == 7 for a, b in zip(dates, dates[1:], strict=False))


@pytest.mark.parametrize(
    "table,field,value",
    [
        ("daily_stock", "closing_stock", "-1"),
        ("daily_stock", "safe_stock", "999999"),
        ("daily_stock", "consumption_observed_quantity", "999999"),
        ("daily_stock", "consumption_window_start", "2020-01-01T00:00:00-03:00"),
        ("daily_stock", "consumption_window_end", "2020-01-01T00:00:00-03:00"),
        ("daily_stock", "daily_consumption", "999999"),
        ("daily_stock", "safe_stock_source", "MANUAL_THRESHOLD"),
        ("price_quotes", "pack_price_clp", "0"),
        ("price_quotes", "unit_price_clp", "1"),
        ("supplier_products", "conversion_factor", "0"),
        ("supplier_products", "content_unit", "kg"),
        ("supplier_products", "units_per_pack", 0),
        ("orders", "supplier_id", 999),
        ("orders", "received_at", "2020-01-01T00:00:00-03:00"),
        ("lots", "shelf_life_days", 0),
        ("lots", "expires_at", "2020-01-01T00:00:00-03:00"),
        ("lots", "remaining_quantity", "-1"),
        ("receipts", "quantity_received", "1"),
        ("receipts", "order_id", "missing"),
        ("waste", "occurred_at", "2020-01-01T00:00:00-03:00"),
        ("waste", "quantity", "0"),
        ("waste", "cause", "fixed_percent"),
        ("demand", "unmet_quantity", "NaN"),
        ("counts", "shortage", "99999"),
        ("movements", "receipt_id", "missing"),
    ],
)
def test_validator_rejects_corrupt_data(dataset, table, field, value, tmp_path) -> None:
    source = dataset
    if table == "waste":
        # Keep expiry-corruption coverage even when the active dataset has zero merma.
        # This is a directed older-lot fixture, never a production-policy adjustment.
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        config["age_at_receipt_days"] = 19
        config["price_snapshot"] = str((CONFIG.parent / config["price_snapshot"]).resolve())
        changed = tmp_path / "expiry_corruption.json"
        changed.write_text(json.dumps(config), encoding="utf-8")
        source = generate(changed, CONFIG.with_name("products.json"))
        assert source["waste"] and validate_dataset(source)["valid"]
    bad = copy.deepcopy(source)
    row = next(r for r in bad[table] if field in r)
    row[field] = value
    report = validate_dataset(bad)
    assert not report["valid"], (table, field)
    assert report["errors"]


def test_missing_weekly_count_duplicate_ids_and_temporal_gap_fail(dataset) -> None:
    for mutation in ("weekly", "duplicate", "gap", "fefo"):
        bad = copy.deepcopy(dataset)
        if mutation == "weekly":
            bad["counts"] = [c for c in bad["counts"] if c["count_type"] != "WEEKLY_PHYSICAL"]
        elif mutation == "duplicate":
            bad["lots"].append(bad["lots"][0])
        elif mutation == "gap":
            bad["demand"].pop(10)
        else:
            bad["allocations"][0]["lot_id"] = bad["lots"][-1]["id"]
        assert not validate_dataset(bad)["valid"]


def test_changed_csv_hash_fails(dataset, tmp_path) -> None:
    export_dataset(dataset, tmp_path)
    with (tmp_path / "demand.csv").open("a", encoding="utf-8") as stream:
        stream.write("tampered\n")
    assert not validate_files(tmp_path)["valid"]


def test_smaller_baskets_can_legitimately_have_zero_expiry(tmp_path) -> None:
    # A causal regression, not a policy simulation or a required KPI target.
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    for order in config["purchase_plan"]:
        order["packs"] = 12
    config["price_snapshot"] = str((CONFIG.parent / config["price_snapshot"]).resolve())
    changed = tmp_path / "config.json"
    changed.write_text(json.dumps(config), encoding="utf-8")
    normal = generate(changed, CONFIG.with_name("products.json"))
    assert normal["waste"] == []
    assert validate_dataset(normal)["valid"]


def test_purchase_quantity_uses_only_a_preperiod_operational_estimate(dataset) -> None:
    from decimal import ROUND_CEILING

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    basis = config["purchase_plan_basis"]
    estimate = json.loads((CONFIG.parent / basis["estimate_snapshot"]).read_text(encoding="utf-8"))
    history = json.loads((CONFIG.parent / estimate["history_snapshot"]).read_text(encoding="utf-8"))
    weekly = sum(D(row["consumed_quantity"]) for row in history["rows"]) / 28 * 7
    expected = int(
        (D(weekly) * D(estimate["ordering_interval_calendar_days"]) / 7 / 30).to_integral_value(
            ROUND_CEILING
        )
    )
    assert weekly == 360 and expected == 24
    assert "recipes" not in estimate  # Purchaser cannot read the generator's true centres.
    assert "basket_minimum_clp" not in basis
    assert basis["commercial_basket_minimum_not_used"] is True
    assert [order["week"] for order in config["purchase_plan"]] == [0, 2, 4, 6, 8]
    assert all(
        order["packs"] == expected and order["delay_days"] == 0 for order in config["purchase_plan"]
    )
    for order in dataset["orders"]:
        if not order["is_opening_balance"]:
            assert datetime.fromisoformat(estimate["as_of"]) < datetime.fromisoformat(
                order["issued_at"]
            )
    assert not any("catering" in order["reason"] for order in dataset["orders"])
    assert {lot["shelf_life_days"] for lot in dataset["lots"]} == {30}


def test_prior_consumption_is_separate_from_future_realization(dataset) -> None:
    estimate = json.loads(
        CONFIG.with_name("estimacion_operacional_preperiodo.json").read_text(encoding="utf-8")
    )
    history = json.loads((CONFIG.parent / estimate["history_snapshot"]).read_text(encoding="utf-8"))
    assert history["seed"] == 20260629 != dataset["metadata"]["seed"]
    assert len(history["rows"]) == history["days"] == 28
    dates = [date.fromisoformat(row["date"]) for row in history["rows"]]
    assert all((b - a).days == 1 for a, b in zip(dates, dates[1:], strict=False))
    assert dates[-1] < date.fromisoformat(dataset["metadata"]["start_date"])
    assert [row["consumed_quantity"] for row in history["rows"]] != [
        int(row["demand_quantity"]) for row in dataset["demand"][:28]
    ]
    assert sum(row["consumed_quantity"] for row in history["rows"]) == 1440


def test_normal_receipts_overlap_before_previous_lot_expiry(dataset) -> None:
    lots = [lot for lot in dataset["lots"] if lot["order_id"] != dataset["orders"][0]["id"]]
    for lot in lots:
        received = datetime.fromisoformat(lot["received_at"]).date()
        expires = datetime.fromisoformat(lot["expires_at"]).date()
        assert (expires - received).days == 25
    for previous, following in zip(lots, lots[1:], strict=False):
        previous_received = datetime.fromisoformat(previous["received_at"]).date()
        next_received = datetime.fromisoformat(following["received_at"]).date()
        previous_expiry = datetime.fromisoformat(previous["expires_at"]).date()
        assert (next_received - previous_received).days == 14
        assert (previous_expiry - next_received).days == 11


def test_future_demand_changes_cannot_change_fixed_purchase_amounts(tmp_path) -> None:
    # A decoupling regression, not a without/with comparison or impact calculation.
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    before = generate(CONFIG)
    for recipe in config["recipes"]:
        recipe["weekday_servings"] = [2 * value for value in recipe["weekday_servings"]]
    config["price_snapshot"] = str((CONFIG.parent / config["price_snapshot"]).resolve())
    changed = tmp_path / "future_demand.json"
    changed.write_text(json.dumps(config), encoding="utf-8")
    after = generate(changed, CONFIG.with_name("products.json"))
    assert after["orders"] == before["orders"]
    assert after["summary"]["demand_quantity"] != before["summary"]["demand_quantity"]


def test_reference_is_exclusive_for_closing_lot_expiry(tmp_path) -> None:
    # Directed boundary fixture: older received eggs, still the same total 30-day life.
    # Keep this regression even though the active policy has no 05/10 expiry.
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    config["age_at_receipt_days"] = 19
    config["price_snapshot"] = str((CONFIG.parent / config["price_snapshot"]).resolve())
    changed = tmp_path / "expiry_boundary.json"
    changed.write_text(json.dumps(config), encoding="utf-8")
    boundary = generate(changed, CONFIG.with_name("products.json"))
    reference = boundary["metadata"]["reference_at"]
    assert any(
        lot["expires_at"] == reference and D(lot["remaining_quantity"]) > 0
        for lot in boundary["lots"]
    )
    assert validate_dataset(boundary)["valid"]
