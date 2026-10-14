"""Reproducible E2 fixture: one inventory history, never a without/with simulator."""

import argparse
import csv
import hashlib
import json
import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from app.domain.forecast import calculate_consumption, local_day_start
from app.domain.replenishment import (
    SupplierLeadTime,
    assess_replenishment,
    maximum_active_lead_time,
)
from app.domain.shortage import calculate_count
from app.e2.contracts import PackSpec
from app.e2.demand import recipe_demand

D = Decimal
TABLES = (
    "items",
    "suppliers",
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
    "daily_stock",
)


def read_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8-sig"), parse_float=D)


def json_text(value: object) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            default=lambda obj: obj.isoformat() if isinstance(obj, (datetime, date)) else str(obj),
        )
        + "\n"
    )


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_text(value), encoding="utf-8", newline="\n")


def generate(config_path: Path, products_path: Path | None = None) -> dict:
    config = read_json(config_path)
    products = read_json(products_path or config_path.with_name("products.json"))
    if config["allocation_policy"] != "FEFO":
        raise ValueError("This perishable fixture requires FEFO")
    start = date.fromisoformat(config["start_date"])
    days = config["days"]
    life = config["shelf_life_days"]
    age = config["age_at_receipt_days"]
    if type(days) is not int or days < 28 or days % 7 or not 0 <= age < life:
        raise ValueError("Use complete weeks, at least 28 days and a positive remaining shelf life")
    base_unit = products["base_unit"]
    if base_unit not in {"unidad", "kg", "L"}:
        raise ValueError("Use a supported physical base unit")
    zone = ZoneInfo(config["timezone"])
    end = start + timedelta(days=days)
    reference = local_day_start(end, zone)
    rng = random.Random(config["seed"])
    weight_rng = random.Random(config.get("weight_seed", config["seed"]))
    opened_life = config.get("opened_shelf_life_days")
    if opened_life is not None and (type(opened_life) is not int or opened_life <= 0):
        raise ValueError("Opened life must be positive calendar days")
    source = read_json((config_path.parent / config["price_snapshot"]).resolve())
    quotes = source["quotes"]
    by_supplier = {s["supplier_id"]: s for s in products["suppliers"]}
    if len(by_supplier) != len(products["suppliers"]):
        raise ValueError("Duplicate supplier IDs")
    packs = {key: PackSpec.model_validate(s["pack"]).to_pack() for key, s in by_supplier.items()}
    prices = {q["supplier_id"]: D(q["pack_price_clp"]) for q in quotes if q["available"]}
    if set(prices) != set(by_supplier):
        raise ValueError(
            "A verified positive price snapshot is required for each configured supplier"
        )
    lead = maximum_active_lead_time(
        [
            SupplierLeadTime(s["supplier_id"], s["lead_time_days"], s["is_active"])
            for s in products["suppliers"]
        ]
    )
    data = {table: [] for table in TABLES}
    data["metadata"] = {
        "schema_version": "2.0",
        "provenance": config["provenance"],
        "start_date": start.isoformat(),
        "end_date_inclusive": (end - timedelta(days=1)).isoformat(),
        "reference_at": reference.isoformat(),
        "timezone": config["timezone"],
        "seed": config["seed"],
        "days": days,
        "allocation_policy": "FEFO",
        "shelf_life_days": life,
        "age_at_receipt_days": age,
        "expiry_basis": config["expiry_basis"],
        "storage": config["storage"],
        "order_price_basis": config["order_price_basis"],
        "purchase_plan_notes": config["purchase_plan_notes"],
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "products_sha256": hashlib.sha256(
            (products_path or config_path.with_name("products.json")).read_bytes()
        ).hexdigest(),
        "no_e3_simulation": True,
    }
    if opened_life is not None:
        data["metadata"].update(
            opened_shelf_life_days=opened_life,
            opening_rule="FIRST_ACTUAL_CONSUMPTION_PER_COMMERCIAL_PIECE",
            weight_seed=config["weight_seed"],
            weight_basis="SYNTHETIC_SCALE_WEIGHTS_NOT_OBSERVED_RECEIPTS",
        )
    for supplier in products["suppliers"]:
        key = supplier["supplier_id"]
        data["suppliers"].append(
            {
                k: supplier[k]
                for k in (
                    "supplier_id",
                    "name",
                    "lead_time_days",
                    "shipping_cost_clp",
                    "is_active",
                    "adapter",
                    "operating_parameters_provenance",
                )
            }
        )
        data["supplier_products"].append(
            {
                "id": supplier["supplier_product_id"],
                "supplier_id": key,
                "item_id": products["item_id"],
                "name_on_site": supplier["name_on_site"],
                "brand": supplier["brand"],
                "sku": supplier["sku"],
                "url": supplier["url"],
                "min_packs": supplier["min_packs"],
                "comparison_group": products["comparison_group"],
                **packs[key].as_dict(),
            }
        )
        if supplier["pack"].get("is_variable_weight"):
            data["supplier_products"][-1]["is_variable_weight"] = True
    data["price_quotes"] = quotes
    opening_key = config["opening_supplier_id"]
    opening_quantity = packs[opening_key].quantity_for_packs(config["opening_packs"])
    stock = opening_quantity
    data["items"].append(
        {
            "id": products["item_id"],
            "name": products["item_name"],
            "base_unit": base_unit,
            "timezone": config["timezone"],
            "observation_started_at": local_day_start(start, zone),
            "initial_quantity": opening_quantity,
            "manual_safe_stock": None,
        }
    )

    def at(day: date, hour: int, minute: int = 0) -> datetime:
        return datetime.combine(day, time(hour, minute), zone)

    def make_order(plan: dict, issued_on: date, opening: bool = False) -> dict:
        supplier = by_supplier[plan["supplier_id"]]
        pack = packs[plan["supplier_id"]]
        number = len(data["orders"]) + 1
        expected_on = issued_on + timedelta(days=supplier["lead_time_days"])
        received_on = expected_on + timedelta(days=plan.get("delay_days", 0))
        order = {
            "id": f"e2-oc-{number:04d}",
            "code": f"OC-E2-{number:04d}",
            "supplier_id": plan["supplier_id"],
            "supplier_product_id": supplier["supplier_product_id"],
            "item_id": products["item_id"],
            "packs": plan["packs"],
            "quantity": pack.quantity_for_packs(plan["packs"]),
            "base_unit": base_unit,
            "pack": supplier["pack"],
            "pack_price_clp": prices[plan["supplier_id"]],
            "total_clp": prices[plan["supplier_id"]] * plan["packs"],
            "price_basis": config["order_price_basis"],
            "provenance": "SYNTHETIC",
            "issued_at": at(issued_on, 9),
            "approved_at": at(issued_on, 9, 5),
            "expected_at": at(expected_on, 10),
            "received_at": at(received_on, 10),
            "lead_time_days": supplier["lead_time_days"],
            "delay_days": plan.get("delay_days", 0),
            "status": "RECEIVED" if received_on < end else "APPROVED",
            "reason": plan["reason"],
            "is_opening_balance": opening,
        }
        data["orders"].append(order)
        return order

    def receive(order: dict, opening: bool = False) -> dict:
        number = len(data["receipts"]) + 1
        received_on = order["received_at"].date()
        produced_on = received_on - timedelta(days=age)
        variable = order["pack"].get("is_variable_weight", False)
        if variable:
            lower, upper = config["synthetic_piece_weight_g_range"]
            quantities = [D(weight_rng.randint(lower, upper)) / 1000 for _ in range(order["packs"])]
        elif opened_life is not None:
            quantities = [packs[order["supplier_id"]].pack_quantity] * order["packs"]
        else:
            quantities = [order["quantity"]]
        lot_ids = []
        for piece_index, quantity in enumerate(quantities, 1):
            lot_id = f"LOT-RCPT-E2-{number:04d}"
            if len(quantities) > 1:
                lot_id += f"-P{piece_index:02d}"
            lot = {
                "id": lot_id,
                "receipt_id": f"RCPT-E2-{number:04d}",
                "order_id": order["id"],
                "item_id": products["item_id"],
                "supplier_id": order["supplier_id"],
                "quantity_received": quantity,
                "received_at": order["received_at"],
                "produced_on": produced_on,
                "shelf_life_days": life,
                "expires_at": local_day_start(produced_on + timedelta(days=life), zone),
                "remaining_quantity": quantity,
                "base_unit": base_unit,
                "expiry_provenance": config["expiry_basis"],
            }
            if opened_life is not None:
                lot.update(
                    closed_expires_at=lot["expires_at"],
                    opened_at=None,
                    opened_shelf_life_days=opened_life,
                    piece_index=piece_index,
                )
            data["lots"].append(lot)
            lot_ids.append(lot_id)
        receipt = {
            "id": f"RCPT-E2-{number:04d}",
            "order_id": order["id"],
            "supplier_id": order["supplier_id"],
            "supplier_product_id": order["supplier_product_id"],
            "item_id": products["item_id"],
            "lot_id": lot_ids[0],
            "received_at": order["received_at"],
            "packs_received": order["packs"],
            "quantity_received": sum(quantities, D(0)),
            "base_unit": base_unit,
            "is_opening_balance": opening,
            "movement_id": None,
        }
        if opened_life is not None or len(lot_ids) > 1:
            receipt.update(lot_ids=lot_ids, piece_quantities=quantities)
        if variable:
            receipt.update(
                is_variable_weight=True,
                nominal_quantity=order["quantity"],
                quantity_basis="SYNTHETIC_MEASURED_WEIGHT",
            )
        data["receipts"].append(receipt)
        return receipt

    def movement(kind: str, delta: D, instant: datetime, **links: object) -> dict:
        row = {
            "id": len(data["movements"]) + 1,
            "item_id": products["item_id"],
            "observation_id": 1,
            "kind": kind,
            "delta": delta,
            "occurred_at": instant,
            "created_at": instant,
            "input_quantity": abs(delta),
            "input_unit": base_unit,
            "balance_after": stock,
            "invalidates_movement_id": None,
            **links,
        }
        data["movements"].append(row)
        return row

    opening_order = make_order(
        {
            "supplier_id": opening_key,
            "packs": config["opening_packs"],
            "reason": "opening_stock_from_preperiod_approved_order",
        },
        start - timedelta(days=by_supplier[opening_key]["lead_time_days"] + 1),
        True,
    )
    opening_receipt = receive(opening_order, True)
    opening_quantity = opening_receipt["quantity_received"]
    stock = opening_quantity
    data["items"][0]["initial_quantity"] = opening_quantity
    initial = movement(
        "INITIAL",
        opening_quantity,
        local_day_start(start, zone),
        receipt_id=opening_receipt["id"],
        order_id=opening_order["id"],
    )
    opening_receipt["movement_id"] = initial["id"]
    for plan in config["purchase_plan"]:
        offset = plan.get("day_offset", plan.get("week", 0) * 7)
        if type(offset) is not int or not 0 <= offset < days:
            raise ValueError("Purchase date is outside the fixture period")
        make_order(plan, start + timedelta(days=offset))
    exits: list[tuple[datetime, D]] = []
    observation = local_day_start(start, zone)
    shortage_episode = False

    def discard_expired(when: datetime) -> D:
        nonlocal stock
        expired = [
            lot
            for lot in data["lots"]
            if lot["remaining_quantity"] > 0 and lot["expires_at"] <= when
        ]
        expired_quantity = sum((lot["remaining_quantity"] for lot in expired), D(0))
        if expired:
            before = stock
            stock -= expired_quantity
            calculated = calculate_count(before, stock)
            adjustment = movement("COUNT_ADJUSTMENT", calculated.difference, when)
            count_id = len(data["counts"]) + 1
            data["counts"].append(
                {
                    "id": count_id,
                    "item_id": products["item_id"],
                    "observation_id": 1,
                    "adjustment_movement_id": adjustment["id"],
                    "theoretical_before": before,
                    "physical_stock": stock,
                    "difference": calculated.difference,
                    "shortage": calculated.shortage,
                    "cause": "vencimiento",
                    "occurred_at": when,
                    "created_at": when,
                    "count_type": "EXPIRY_RECONCILIATION",
                    "needs_review": False,
                }
            )
            for lot in expired:
                data["waste"].append(
                    {
                        "id": f"WASTE-{len(data['waste']) + 1:04d}",
                        "lot_id": lot["id"],
                        "receipt_id": lot["receipt_id"],
                        "count_id": count_id,
                        "movement_id": adjustment["id"],
                        "occurred_at": when,
                        "quantity": lot["remaining_quantity"],
                        "cause": "vencimiento",
                        "base_unit": base_unit,
                    }
                )
                lot["remaining_quantity"] = D(0)
        return expired_quantity

    for index in range(days):
        day = start + timedelta(days=index)
        opening_stock = stock
        expired_quantity = discard_expired(at(day, 7, 30))
        received_quantity = D(0)
        for order in data["orders"]:
            if not order["is_opening_balance"] and order["received_at"].date() == day:
                receipt = receive(order)
                stock += receipt["quantity_received"]
                entry = movement(
                    "ENTRY",
                    receipt["quantity_received"],
                    order["received_at"],
                    receipt_id=receipt["id"],
                    order_id=order["id"],
                )
                receipt["movement_id"] = entry["id"]
                received_quantity += receipt["quantity_received"]
                shortage_episode = False
        recipe_details = recipe_demand(config["recipes"], day.weekday(), rng, base_unit)
        wanted = D(sum(r["demand_quantity"] for r in recipe_details))
        # Opened lots expire at their original opening clock time, not at reception.
        expired_quantity += discard_expired(at(day, 18))
        available = stock
        fulfilled = min(wanted, available)
        unmet = wanted - fulfilled
        demand_id = f"DEM-{day.isoformat()}"
        to_allocate = fulfilled
        allocation_start = len(data["allocations"])
        for lot in sorted(
            data["lots"], key=lambda row: (row["expires_at"], row["received_at"], row["id"])
        ):
            if to_allocate == 0:
                break
            if lot["expires_at"] <= at(day, 18) or lot["remaining_quantity"] == 0:
                continue
            used = min(lot["remaining_quantity"], to_allocate)
            if opened_life is not None and used > 0 and lot["opened_at"] is None:
                lot["opened_at"] = at(day, 18)
                lot["expires_at"] = min(
                    lot["closed_expires_at"], lot["opened_at"] + timedelta(days=opened_life)
                )
            lot["remaining_quantity"] -= used
            to_allocate -= used
            data["allocations"].append(
                {
                    "id": len(data["allocations"]) + 1,
                    "demand_id": demand_id,
                    "lot_id": lot["id"],
                    "quantity": used,
                    "occurred_at": at(day, 18),
                    "movement_id": None,
                }
            )
        if to_allocate != 0:
            raise ValueError("Usable lots and inventory balance diverged")
        stock -= fulfilled
        exit_id = None
        if fulfilled > 0:
            exit_row = movement("EXIT", -fulfilled, at(day, 18), demand_id=demand_id)
            exit_id = exit_row["id"]
            exits.append((at(day, 18), fulfilled))
            for allocation in data["allocations"][allocation_start:]:
                allocation["movement_id"] = exit_id
        new_zero_episode = stock == 0 and available > 0 and not shortage_episode
        if stock == 0:
            shortage_episode = True
        data["demand"].append(
            {
                "id": demand_id,
                "date": day.isoformat(),
                "item_id": products["item_id"],
                "demand_quantity": wanted,
                "available_quantity": available,
                "fulfilled_quantity": fulfilled,
                "unmet_quantity": unmet,
                "exit_movement_id": exit_id,
                "stockout_day": unmet > 0,
                "new_zero_stock_episode": new_zero_episode,
                "recipe_details": recipe_details,
                "base_unit": base_unit,
                "provenance": "SYNTHETIC",
            }
        )
        if index % 7 == 6:
            calculated = calculate_count(stock, stock)
            adjustment = movement("COUNT_ADJUSTMENT", D(0), at(day, 20))
            data["counts"].append(
                {
                    "id": len(data["counts"]) + 1,
                    "item_id": products["item_id"],
                    "observation_id": 1,
                    "adjustment_movement_id": adjustment["id"],
                    "theoretical_before": stock,
                    "physical_stock": stock,
                    "difference": calculated.difference,
                    "shortage": calculated.shortage,
                    "cause": "desconocida",
                    "occurred_at": at(day, 20),
                    "created_at": at(day, 20),
                    "count_type": "WEEKLY_PHYSICAL",
                    "needs_review": False,
                }
            )
        consumption = calculate_consumption(observation, at(day, 21), config["timezone"], exits)
        in_transit = sum(
            (
                order["quantity"]
                for order in data["orders"]
                if order["approved_at"] <= at(day, 21) < order["received_at"]
            ),
            D(0),
        )
        assessment = assess_replenishment(
            stock,
            None,
            consumption,
            at(day, 21),
            lead_time_days=lead,
            in_transit_quantity=in_transit,
        )
        data["daily_stock"].append(
            {
                "date": day.isoformat(),
                "opening_stock": opening_stock,
                "received_quantity": received_quantity,
                "expired_quantity": expired_quantity,
                "fulfilled_quantity": fulfilled,
                "unmet_quantity": unmet,
                "closing_stock": stock,
                "consumption_complete_days": consumption.complete_days,
                "consumption_status": consumption.status,
                "consumption_window_start": consumption.window_start,
                "consumption_window_end": consumption.window_end,
                "consumption_observed_quantity": consumption.observed_quantity,
                "weekly_consumption": consumption.weekly_average,
                "daily_consumption": assessment.daily_average,
                "safe_stock": assessment.safe_stock,
                "safe_stock_source": assessment.alert_source,
                "lead_time_days": lead,
                "projected_arrival_stock": assessment.projected_arrival_stock,
                "in_transit_quantity": in_transit,
                "alert_status": assessment.alert_status,
                "is_low_stock": assessment.is_low_stock,
                "required_quantity": assessment.required_quantity,
                "suggested_quantity": assessment.suggested_quantity,
            }
        )
    demand_total = sum((row["demand_quantity"] for row in data["demand"]), D(0))
    waste_total = sum((row["quantity"] for row in data["waste"]), D(0))
    total_received = sum((row["quantity_received"] for row in data["receipts"]), D(0))
    final_consumption = calculate_consumption(observation, reference, config["timezone"], exits)
    data["summary"] = {
        "demand_quantity": demand_total,
        "fulfilled_quantity": sum((row["fulfilled_quantity"] for row in data["demand"]), D(0)),
        "unmet_quantity": sum((row["unmet_quantity"] for row in data["demand"]), D(0)),
        "waste_quantity": waste_total,
        "received_quantity_including_opening": total_received,
        "final_stock": stock,
        "weekly_physical_counts": days // 7,
        "stockout_days": sum(row["stockout_day"] for row in data["demand"]),
        "zero_stock_episodes": sum(row["new_zero_stock_episode"] for row in data["demand"]),
        "unmet_demand_percent": D(100)
        * sum((r["unmet_quantity"] for r in data["demand"]), D(0))
        / demand_total,
        "expiry_waste_percent_of_available": D(100) * waste_total / total_received,
        "last_28_days_consumption": final_consumption.observed_quantity,
        "automatic_safe_stock_at_reference": final_consumption.weekly_average,
        "provenance": "CALCULATED_DESCRIPTION_OF_SYNTHETIC_E2_FIXTURE_NOT_E3_IMPACT",
    }
    # Round-trip makes generated JSON and in-memory validator inputs identical.
    return json.loads(json_text(data))


def export_dataset(data: dict, destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    write_json(destination / "dataset.json", data)
    for table in TABLES:
        rows = data[table]
        fields = list(dict.fromkeys(key for row in rows for key in row)) or ["id"]
        with (destination / f"{table}.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        k: json.dumps(v, ensure_ascii=False, sort_keys=True)
                        if isinstance(v, (dict, list))
                        else ("" if v is None else v)
                        for k, v in row.items()
                    }
                )
    manifest = {
        "schema_version": "2.0",
        "provenance": data["metadata"]["provenance"],
        "row_counts": {table: len(data[table]) for table in TABLES},
        "files": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(destination.glob("*.csv")) + [destination / "dataset.json"]
        },
    }
    write_json(destination / "manifest.json", manifest)
    write_json(destination / "summary.json", data["summary"])
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--products", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    from app.e2.validation import validate_dataset

    data = generate(args.config, args.products)
    report = validate_dataset(data)
    if not report["valid"]:
        raise SystemExit(json_text(report))
    manifest = export_dataset(data, args.out)
    write_json(args.out / "validation.json", report)
    print(
        json_text(
            {"row_counts": manifest["row_counts"], "validation": report, "summary": data["summary"]}
        )
    )


if __name__ == "__main__":
    main()
