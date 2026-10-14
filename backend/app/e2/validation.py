"""Independent balance, expiry, relationship and calendar checks for Dataset v2."""

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from app.domain.errors import RuleViolation
from app.domain.forecast import calculate_consumption
from app.domain.history import CountFact, MovementFact, validate_history
from app.domain.replenishment import (
    SupplierLeadTime,
    assess_replenishment,
    maximum_active_lead_time,
)
from app.e2.contracts import PackSpec

D = Decimal


def instant(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Date must include a timezone")
    return result


def number(value: object, *, positive: bool = False) -> D:
    result = D(str(value))
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError("Expected a finite nonnegative quantity, or positive when required")
    return result


def validate_dataset(data: dict) -> dict:
    errors: list[str] = []
    checks: list[str] = []

    def check(condition: bool, description: str) -> None:
        if not condition:
            errors.append(description)

    def close(actual: object, expected: D | None, description: str) -> None:
        if expected is None:
            check(actual is None, description)
        else:
            check(
                actual is not None
                and D(str(actual)).is_finite()
                and abs(D(str(actual)) - expected) <= D("0.000001"),
                description,
            )

    try:
        meta = data["metadata"]
        check(
            meta["schema_version"] == "2.0" and meta["no_e3_simulation"] is True,
            "Dataset must be v2 and exclude E3 scenarios",
        )
        check(
            meta["provenance"] == "SYNTHETIC_E2_TEST_DATA_NOT_A_REAL_BUSINESS",
            "Synthetic data must be declared",
        )
        start = date.fromisoformat(meta["start_date"])
        end = date.fromisoformat(meta["end_date_inclusive"]) + timedelta(days=1)
        zone = ZoneInfo(meta["timezone"])
        reference = instant(meta["reference_at"])
        check(
            reference.astimezone(zone).date() == end, "Reference must follow the final complete day"
        )
        check(
            (end - start).days == meta["days"] and meta["days"] >= 28 and meta["days"] % 7 == 0,
            "Period must contain complete weeks and at least 28 days",
        )
        checks.append("synthetic provenance, schema, timezone and continuous period")
        index = {}
        for table in (
            "items",
            "supplier_products",
            "price_quotes",
            "orders",
            "receipts",
            "lots",
            "movements",
            "counts",
            "demand",
            "allocations",
            "waste",
        ):
            rows = data[table]
            ids = [r["id"] for r in rows]
            check(
                all(type(value) in (int, str) and value not in (0, "") for value in ids),
                f"{table}: invalid IDs",
            )
            check(len(set(ids)) == len(ids), f"{table}: duplicate IDs")
            index[table] = {r["id"]: r for r in rows}
        suppliers = {s["supplier_id"]: s for s in data["suppliers"]}
        check(len(suppliers) == len(data["suppliers"]), "Duplicate supplier IDs")
        check(
            {s["name"] for s in suppliers.values()}
            == {"Central Mayorista", "Jumbo", "Santa Isabel"},
            "Only the three required suppliers belong to E2",
        )
        check(len(data["items"]) == 1, "MVP fixture must have one item")
        item = data["items"][0]
        check(
            item["base_unit"] in {"unidad", "kg", "L"} and item["manual_safe_stock"] is None,
            "Use compatible physical base units and automatic safe stock",
        )
        for table in (
            "supplier_products",
            "orders",
            "receipts",
            "lots",
            "movements",
            "counts",
            "demand",
        ):
            check(all(r["item_id"] == item["id"] for r in data[table]), f"{table}: orphan item_id")
        checks.append("unique IDs, required suppliers and entity/item foreign keys")
        pack_by_product = {}
        for product in data["supplier_products"]:
            check(product["supplier_id"] in suppliers, "Product supplier does not exist")
            pack = PackSpec.model_validate(
                {
                    k: product[k]
                    for k in (
                        "commercial_format",
                        "content_quantity",
                        "content_unit",
                        "units_per_pack",
                        "base_unit",
                    )
                }
            ).to_pack()
            pack_by_product[product["id"]] = pack
            check(
                number(product["pack_quantity"], positive=True) == pack.pack_quantity,
                "Commercial quantity is not convertible to base unit",
            )
            check(
                number(product["conversion_factor"], positive=True) == pack.conversion_factor,
                "Conversion factor is wrong",
            )
            check(
                type(product["min_packs"]) is int and product["min_packs"] > 0,
                "Minimum packs must be a positive integer",
            )
        for quote in data["price_quotes"]:
            product = index["supplier_products"][quote["supplier_product_id"]]
            check(quote["supplier_id"] == product["supplier_id"], "Quote has the wrong supplier")
            price = number(quote["pack_price_clp"], positive=True)
            pack = pack_by_product[product["id"]]
            check(
                number(quote["unit_price_clp"], positive=True) == pack.unit_price(price),
                "Price normalization is wrong",
            )
            check(
                number(quote["pack_quantity"], positive=True) == pack.pack_quantity,
                "Quote format differs from product format",
            )
            check(
                quote["currency"] == "CLP" and quote["base_unit"] == item["base_unit"],
                "Price currency/unit is incompatible",
            )
            check(
                quote["url"] == product["url"] and quote["acquisition"] == "PUBLIC_HTTP_GET",
                "Quote lacks public source provenance",
            )
            observed = instant(quote["observed_at"])
            check(
                instant(quote["valid_until"]) == observed + timedelta(days=7),
                "Price TTL must be seven days from original observation",
            )
        checks.append(
            "positive prices, full packs, compatible units, conversion and price normalization"
        )
        for order in data["orders"]:
            product = index["supplier_products"][order["supplier_product_id"]]
            supplier = suppliers[order["supplier_id"]]
            check(order["supplier_id"] == product["supplier_id"], "Order product/supplier mismatch")
            check(order["base_unit"] == product["base_unit"], "Order unit mismatch")
            quantity = pack_by_product[product["id"]].quantity_for_packs(order["packs"])
            check(
                number(order["quantity"], positive=True) == quantity,
                "Order quantity/format mismatch",
            )
            check(
                number(order["total_clp"], positive=True)
                == number(order["pack_price_clp"], positive=True) * order["packs"],
                "Order price mismatch",
            )
            issued, approved, expected, received = [
                instant(order[k])
                for k in ("issued_at", "approved_at", "expected_at", "received_at")
            ]
            check(
                issued <= approved <= expected <= received,
                "Order/approval/expected/receipt dates reversed",
            )
            check(
                order["lead_time_days"] == supplier["lead_time_days"],
                "Order lead time differs from supplier",
            )
            check(
                (expected.astimezone(zone).date() - issued.astimezone(zone).date()).days
                == supplier["lead_time_days"],
                "Order lead time must use calendar days",
            )
            check(
                (received.astimezone(zone).date() - expected.astimezone(zone).date()).days
                == order["delay_days"],
                "Actual receipt delay is inconsistent",
            )
            check(
                order["price_basis"] == meta["order_price_basis"]
                and order["provenance"] == "SYNTHETIC",
                "Historical order prices must not be presented as observed",
            )
        receipts_by_order: dict[str, list] = defaultdict(list)
        for receipt in data["receipts"]:
            order = index["orders"][receipt["order_id"]]
            receipts_by_order[order["id"]].append(receipt)
            check(order["status"] == "RECEIVED", "Reception requires an approved/received OC")
            check(
                receipt["supplier_id"] == order["supplier_id"]
                and receipt["supplier_product_id"] == order["supplier_product_id"],
                "Receipt supplier/product mismatch",
            )
            check(
                instant(receipt["received_at"]) == instant(order["received_at"]),
                "Receipt date differs from OC",
            )
            check(
                instant(receipt["received_at"]) >= instant(order["approved_at"]),
                "Receipt precedes approval",
            )
            check(
                receipt["packs_received"] == order["packs"],
                "Full commercial receipt count differs from OC",
            )
            if order["pack"].get("is_variable_weight"):
                check(
                    receipt.get("is_variable_weight") is True
                    and receipt.get("quantity_basis") == "SYNTHETIC_MEASURED_WEIGHT",
                    "Variable product needs explicit weighed receipt",
                )
                check(
                    number(receipt["nominal_quantity"], positive=True)
                    == number(order["quantity"], positive=True),
                    "Nominal weight reference differs from OC",
                )
                check(
                    sum(number(x, positive=True) for x in receipt["piece_quantities"])
                    == number(receipt["quantity_received"], positive=True),
                    "Piece weights differ from received mass",
                )
            else:
                check(
                    number(receipt["quantity_received"], positive=True)
                    == number(order["quantity"], positive=True),
                    "Fixed full receipt quantity differs from OC",
                )
            movement = index["movements"][receipt["movement_id"]]
            check(
                movement["receipt_id"] == receipt["id"] and movement["order_id"] == order["id"],
                "Receipt is not linked to its inventory movement",
            )
            check(
                movement["kind"] == ("INITIAL" if receipt["is_opening_balance"] else "ENTRY"),
                "Inventory entries must occur on receipt, with opening snapshot distinguished",
            )
            if not receipt["is_opening_balance"]:
                check(
                    instant(movement["occurred_at"]) == instant(receipt["received_at"]),
                    "ENTRY time must be receipt time, not supplier dispatch",
                )
        for order in data["orders"]:
            check(
                len(receipts_by_order[order["id"]]) == (1 if order["status"] == "RECEIVED" else 0),
                "Order reception is missing or duplicated",
            )
        checks.append(
            "OC supplier, quantities, approval, calendar lead time, receipt and inventory linkage"
        )
        allocations_by_lot: dict[str, D] = defaultdict(lambda: D(0))
        allocations_by_demand: dict[str, D] = defaultdict(lambda: D(0))
        waste_by_lot: dict[str, D] = defaultdict(lambda: D(0))
        waste_by_count: dict[int, D] = defaultdict(lambda: D(0))
        waste_by_date: dict[date, D] = defaultdict(lambda: D(0))
        for lot in data["lots"]:
            receipt = index["receipts"][lot["receipt_id"]]
            life = lot["shelf_life_days"]
            produced = date.fromisoformat(lot["produced_on"])
            received = instant(lot["received_at"]).astimezone(zone).date()
            expiry_at = instant(lot["expires_at"])
            closed_at = instant(lot.get("closed_expires_at", lot["expires_at"]))
            expiry = closed_at.astimezone(zone).date()
            check(type(life) is int and life > 0, "Shelf life must be positive")
            check(
                produced <= received < expiry and (expiry - produced).days == life,
                "Lot production, receipt and expiry are inconsistent",
            )
            check(
                (received - produced).days == meta["age_at_receipt_days"]
                and life == meta["shelf_life_days"],
                "Lot shelf life/age must follow declared configuration",
            )
            check(
                lot["id"] in receipt.get("lot_ids", [receipt["lot_id"]])
                and receipt["order_id"] == lot["order_id"],
                "Lot receipt/OC mismatch",
            )
            number(lot["quantity_received"], positive=True)
            if "opened_shelf_life_days" in lot:
                cap = lot["opened_shelf_life_days"]
                check(
                    type(cap) is int and cap > 0 and cap == meta.get("opened_shelf_life_days"),
                    "Opened life must match declared rule",
                )
                opened = instant(lot["opened_at"]) if lot["opened_at"] is not None else None
                if opened is not None:
                    first_use = min(
                        (
                            instant(a["occurred_at"])
                            for a in data["allocations"]
                            if a["lot_id"] == lot["id"]
                        ),
                        default=None,
                    )
                    check(
                        opened == first_use and opened >= instant(lot["received_at"]),
                        "Open only at first actual use, never automatically on receipt",
                    )
                    local_open = opened.astimezone(zone)
                    effective = min(closed_at, local_open + timedelta(days=cap))
                    check(
                        expiry_at == effective,
                        "Effective expiry must respect both closed and opened deadlines",
                    )
                else:
                    check(
                        expiry_at == closed_at
                        and not any(a["lot_id"] == lot["id"] for a in data["allocations"]),
                        "Unopened lot has consumption or changed expiry",
                    )
            number(lot["remaining_quantity"])
        for receipt in data["receipts"]:
            linked = [index["lots"][key] for key in receipt.get("lot_ids", [receipt["lot_id"]])]
            check(
                sum(number(piece["quantity_received"], positive=True) for piece in linked)
                == number(receipt["quantity_received"], positive=True),
                "Lot masses differ from reception",
            )
            if "piece_quantities" in receipt:
                check(
                    len(linked) == receipt["packs_received"]
                    and [number(piece["quantity_received"]) for piece in linked]
                    == [number(x) for x in receipt["piece_quantities"]],
                    "Opening-sensitive product must have one lot per physical piece",
                )
        for allocation in data["allocations"]:
            lot = index["lots"][allocation["lot_id"]]
            demand = index["demand"][allocation["demand_id"]]
            when = instant(allocation["occurred_at"])
            check(
                instant(lot["received_at"]) <= when < instant(lot["expires_at"]),
                "Consumption before receipt or at/after expiry",
            )
            check(
                allocation["movement_id"] == demand["exit_movement_id"],
                "Consumption allocation not linked to EXIT",
            )
            quantity = number(allocation["quantity"], positive=True)
            allocations_by_lot[lot["id"]] += quantity
            allocations_by_demand[demand["id"]] += quantity
        for waste in data["waste"]:
            lot = index["lots"][waste["lot_id"]]
            count = index["counts"][waste["count_id"]]
            when = instant(waste["occurred_at"])
            quantity = number(waste["quantity"], positive=True)
            check(
                when >= instant(lot["expires_at"]) and waste["cause"] == "vencimiento",
                "Waste must arise from an expired lot",
            )
            check(
                waste["receipt_id"] == lot["receipt_id"]
                and waste["movement_id"] == count["adjustment_movement_id"],
                "Waste lacks receipt/count/adjustment linkage",
            )
            check(when == instant(count["occurred_at"]), "Waste/count timestamps mismatch")
            waste_by_lot[lot["id"]] += quantity
            waste_by_count[count["id"]] += quantity
            waste_by_date[when.astimezone(zone).date()] += quantity
        for lot in data["lots"]:
            check(
                number(lot["quantity_received"], positive=True)
                == allocations_by_lot[lot["id"]]
                + waste_by_lot[lot["id"]]
                + number(lot["remaining_quantity"]),
                "Lot mass balance is invalid",
            )
            # reference is the exclusive end of the recorded period, not a next-day discard.
            if instant(lot["expires_at"]) < reference:
                check(number(lot["remaining_quantity"]) == 0, "Expired lot remains available")
        # Reconstruct each allocation from events to verify FEFO, independently of generator state.
        prior_allocations: dict[str, D] = defaultdict(lambda: D(0))

        def expiry_as_of(lot: dict, when: datetime) -> datetime:
            if lot.get("opened_at") is not None and when < instant(lot["opened_at"]):
                return instant(lot["closed_expires_at"])
            return instant(lot["expires_at"])

        for allocation in sorted(
            data["allocations"], key=lambda r: (instant(r["occurred_at"]), r["id"])
        ):
            when = instant(allocation["occurred_at"])
            eligible = [
                lot
                for lot in data["lots"]
                if instant(lot["received_at"]) <= when < expiry_as_of(lot, when)
                and number(lot["quantity_received"]) > prior_allocations[lot["id"]]
            ]
            ordered = sorted(
                eligible,
                key=lambda r: (expiry_as_of(r, when), instant(r["received_at"]), r["id"]),
            )
            check(
                bool(ordered) and allocation["lot_id"] == ordered[0]["id"],
                "Allocation violates FEFO",
            )
            prior_allocations[allocation["lot_id"]] += number(allocation["quantity"], positive=True)
        checks.append(
            "positive shelf life, expiry dates, FEFO, lot mass balances and expiry-only waste"
        )
        balances = D(0)
        facts = []
        for movement in sorted(
            data["movements"], key=lambda r: (instant(r["occurred_at"]), r["id"])
        ):
            delta = D(movement["delta"])
            check(delta.is_finite(), "Nonfinite movement delta")
            check(
                movement["kind"] in {"INITIAL", "ENTRY", "EXIT", "COUNT_ADJUSTMENT"},
                "Unexpected movement kind",
            )
            balances += delta
            check(
                balances >= 0 and number(movement["balance_after"]) == balances,
                "Negative or inconsistent ledger stock",
            )
            check(
                instant(movement["occurred_at"]) == instant(movement["created_at"]),
                "Fixture recording chronology is inconsistent",
            )
            if movement["kind"] == "ENTRY":
                check(
                    movement.get("receipt_id") in index["receipts"] and delta > 0,
                    "ENTRY without receipt",
                )
            elif movement["kind"] == "EXIT":
                check(
                    movement.get("demand_id") in index["demand"] and delta < 0,
                    "EXIT without fulfilled demand",
                )
            facts.append(
                MovementFact(
                    movement["id"],
                    movement["kind"],
                    delta,
                    instant(movement["occurred_at"]),
                    instant(movement["created_at"]),
                    None,
                )
            )
        count_facts = []
        weekly = []
        for count in data["counts"]:
            theoretical, physical = (
                number(count["theoretical_before"]),
                number(count["physical_stock"]),
            )
            difference = D(count["difference"])
            shortage = number(count["shortage"])
            check(
                difference == physical - theoretical
                and shortage == max(theoretical - physical, D(0)),
                "Physical count/difference/shortage mismatch",
            )
            check(
                shortage == waste_by_count[count["id"]],
                "Count shortage must be explained by lot expiry",
            )
            check(
                count["needs_review"] == (physical > theoretical),
                "Positive count review flag incorrect",
            )
            adjustment = index["movements"][count["adjustment_movement_id"]]
            check(
                adjustment["kind"] == "COUNT_ADJUSTMENT" and D(adjustment["delta"]) == difference,
                "Physical count has no matching adjustment",
            )
            count_facts.append(CountFact(count["adjustment_movement_id"], theoretical, physical))
            if count["count_type"] == "WEEKLY_PHYSICAL":
                weekly.append(instant(count["occurred_at"]).astimezone(zone).date())
        expected_weekly = [start + timedelta(days=i) for i in range(6, meta["days"], 7)]
        check(
            sorted(weekly) == expected_weekly,
            "Physical counts must occur every seven calendar days",
        )
        check(
            validate_history(facts, count_facts) == balances, "Existing D1/D6 history rules failed"
        )
        checks.append(
            "all ledger balances nonnegative, P3 counts/adjustments and exact weekly periodicity"
        )
        expected_dates = [(start + timedelta(days=i)).isoformat() for i in range(meta["days"])]
        check(
            [r["date"] for r in data["demand"]] == expected_dates,
            "Demand has missing/duplicate temporal rows",
        )
        check(
            [r["date"] for r in data["daily_stock"]] == expected_dates,
            "Stock has missing/duplicate temporal rows",
        )
        exits = [
            (instant(r["occurred_at"]), -D(r["delta"]))
            for r in data["movements"]
            if r["kind"] == "EXIT"
        ]
        lead = maximum_active_lead_time(
            [
                SupplierLeadTime(s["supplier_id"], s["lead_time_days"], s["is_active"])
                for s in suppliers.values()
            ]
        )
        previous_stock = number(item["initial_quantity"])
        for demand, daily in zip(data["demand"], data["daily_stock"], strict=True):
            wanted = number(demand["demand_quantity"])
            available = number(demand["available_quantity"])
            fulfilled, unmet = (
                number(demand["fulfilled_quantity"]),
                number(demand["unmet_quantity"]),
            )
            check(
                fulfilled == min(wanted, available) and unmet == wanted - fulfilled,
                "Lost sales must equal demand minus stock-capped consumption",
            )
            check(
                demand["stockout_day"] == (unmet > 0), "Stockout flag does not match unmet demand"
            )
            check(
                allocations_by_demand[demand["id"]] == fulfilled,
                "Allocated quantity differs from fulfilled demand",
            )
            check(
                wanted
                == sum(
                    D(r["servings"])
                    * D(
                        r["eggs_per_serving"]
                        if "eggs_per_serving" in r
                        else r["quantity_per_serving"]
                    )
                    for r in demand["recipe_details"]
                ),
                "Demand does not equal documented recipe quantities",
            )
            if demand["exit_movement_id"] is not None:
                exit_row = index["movements"][demand["exit_movement_id"]]
                check(
                    exit_row["kind"] == "EXIT" and -D(exit_row["delta"]) == fulfilled,
                    "Fulfilled consumption/EXIT mismatch",
                )
            else:
                check(fulfilled == 0, "Positive consumption has no EXIT")
            day = date.fromisoformat(daily["date"])
            received = sum(
                (
                    number(r["quantity_received"])
                    for r in data["receipts"]
                    if not r["is_opening_balance"]
                    and instant(r["received_at"]).astimezone(zone).date() == day
                ),
                D(0),
            )
            expired = waste_by_date[day]
            closing = number(daily["closing_stock"])
            check(
                number(daily["opening_stock"]) == previous_stock
                and closing == previous_stock + received - expired - fulfilled,
                "Daily stock recurrence is invalid",
            )
            check(
                number(daily["received_quantity"]) == received
                and number(daily["expired_quantity"]) == expired,
                "Daily receipts/waste do not match source events",
            )
            check(
                number(daily["fulfilled_quantity"]) == fulfilled
                and number(daily["unmet_quantity"]) == unmet,
                "Daily stock differs from demand/consumption",
            )
            previous_stock = closing
            when = datetime.combine(day, datetime.min.time(), zone).replace(hour=21)
            consumption = calculate_consumption(
                instant(item["observation_started_at"]), when, meta["timezone"], exits
            )
            transit = sum(
                (
                    number(order["quantity"])
                    for order in data["orders"]
                    if instant(order["approved_at"]) <= when < instant(order["received_at"])
                ),
                D(0),
            )
            assessment = assess_replenishment(
                closing, None, consumption, when, lead_time_days=lead, in_transit_quantity=transit
            )
            check(
                daily["consumption_complete_days"] == consumption.complete_days
                and daily["consumption_status"] == consumption.status,
                "Consumption observation window is wrong",
            )
            for field, expected_window in (
                ("consumption_window_start", consumption.window_start),
                ("consumption_window_end", consumption.window_end),
            ):
                actual_window = instant(daily[field]) if daily[field] is not None else None
                check(actual_window == expected_window, "Stored consumption window dates are wrong")
            close(
                daily["daily_consumption"], assessment.daily_average, "Daily consumption is wrong"
            )
            check(
                daily["safe_stock_source"] == assessment.alert_source,
                "Automatic safe stock source is incorrectly declared",
            )
            close(
                daily["consumption_observed_quantity"],
                consumption.observed_quantity,
                "28-day consumption sum is wrong",
            )
            close(
                daily["weekly_consumption"],
                consumption.weekly_average,
                "28-day weekly normalization is wrong",
            )
            close(
                daily["safe_stock"],
                consumption.weekly_average,
                "Safe stock must be automatic weekly consumption",
            )
            close(
                daily["projected_arrival_stock"],
                assessment.projected_arrival_stock,
                "Arrival stock projection is wrong",
            )
            close(
                daily["required_quantity"],
                assessment.required_quantity,
                "C5 raw required quantity is wrong",
            )
            close(
                daily["suggested_quantity"],
                assessment.suggested_quantity,
                "C1 in-transit suppression is wrong",
            )
            check(
                daily["lead_time_days"] == lead
                and daily["alert_status"] == assessment.alert_status
                and daily["is_low_stock"] == assessment.is_low_stock,
                "C12 alert/max-active/calendar/equality rule mismatch",
            )
            close(daily["in_transit_quantity"], transit, "In-transit quantity is wrong")
        check(previous_stock == balances, "Closing stock and movement ledger diverge")
        final = calculate_consumption(
            instant(item["observation_started_at"]), reference, meta["timezone"], exits
        )
        total_received = sum((number(r["quantity_received"]) for r in data["receipts"]), D(0))
        total_waste = sum(waste_by_lot.values(), D(0))
        total_demand = sum((number(r["demand_quantity"]) for r in data["demand"]), D(0))
        total_fulfilled = sum((number(r["fulfilled_quantity"]) for r in data["demand"]), D(0))
        total_unmet = total_demand - total_fulfilled
        expected_summary = {
            "demand_quantity": total_demand,
            "fulfilled_quantity": total_fulfilled,
            "unmet_quantity": total_unmet,
            "waste_quantity": total_waste,
            "received_quantity_including_opening": total_received,
            "final_stock": balances,
            "last_28_days_consumption": final.observed_quantity,
            "automatic_safe_stock_at_reference": final.weekly_average,
            "unmet_demand_percent": D(100) * total_unmet / total_demand,
            "expiry_waste_percent_of_available": D(100) * total_waste / total_received,
        }
        for key, expected in expected_summary.items():
            close(data["summary"][key], expected, f"Summary {key} does not reproduce")
        check(
            data["summary"]["stockout_days"] == sum(row["stockout_day"] for row in data["demand"]),
            "Summary stockout days differ from unmet-demand days",
        )
        check(
            data["summary"]["weekly_physical_counts"] == len(weekly),
            "Summary weekly physical count differs from actual counts",
        )
        check(
            total_received == balances + total_waste + total_fulfilled,
            "Overall stock conservation failed",
        )
        checks.extend(
            [
                "continuous demand and stock, recipes, fulfilled demand and explicit lost sales",
                "last 28 complete local days, current-day exclusion and automatic safe stock",
                "C1/C5/C12 arrival projection, maximum active calendar lead time "
                "and in-transit orders",
                "global stock conservation and independently recalculated descriptive summaries",
            ]
        )
    except (KeyError, TypeError, ValueError, InvalidOperation, RuleViolation, IndexError) as exc:
        errors.append(f"Invalid schema/value/reference: {type(exc).__name__}: {exc}")
    return {
        "valid": not errors,
        "checks_completed": len(checks),
        "checks": checks,
        "errors": errors,
        "error_count": len(errors),
    }


def validate_files(destination: Path) -> dict:
    data = json.loads((destination / "dataset.json").read_text(encoding="utf-8"))
    report = validate_dataset(data)
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        path = destination / name
        if (
            path.name != name
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            report["errors"].append(f"Missing/changed exported file: {name}")
    for table, expected in manifest["row_counts"].items():
        if len(data.get(table, [])) != expected:
            report["errors"].append(f"Manifest row count differs: {table}")
        csv_path = destination / f"{table}.csv"
        if csv_path.is_file():
            with csv_path.open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                fields = reader.fieldnames or []
                exported_rows = list(reader)
            expected_rows = []
            for row in data.get(table, []):
                expected_rows.append(
                    {
                        key: json.dumps(row.get(key), ensure_ascii=False, sort_keys=True)
                        if isinstance(row.get(key), (dict, list))
                        else ("" if row.get(key) is None else str(row[key]))
                        for key in fields
                    }
                )
            if exported_rows != expected_rows:
                report["errors"].append(f"CSV and canonical JSON differ: {table}")
    report["error_count"] = len(report["errors"])
    report["valid"] = not report["errors"]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, required=True, help="Directory containing dataset.json"
    )
    args = parser.parse_args()
    report = validate_files(args.dataset)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["valid"] else 1)


if __name__ == "__main__":
    main()
