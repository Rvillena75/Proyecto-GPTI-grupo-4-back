"""Close the synthetic cost evaluation with equal-pack orders and inventory book values."""

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from comparar_politicas import (
    HERE,
    build_demand,
    load_inputs,
    moment,
    piece_weight,
    plan_weights,
    run_policy,
)
from explorar_politicas import Policy, run_candidate

D = Decimal


@dataclass
class ValuedLot:
    quantity: Decimal
    expires: datetime
    closed_expires: datetime
    unit_book_cost: Decimal
    number: int
    opened: bool = False


def equal_order(quantity_packs: int = 8) -> dict:
    """Reference tariffs and context assumptions; no assertion of actual checkout eligibility."""
    if quantity_packs != 8:
        raise ValueError("Solo ocho paquetes cumplen el piso de referencia y el máximo de 1 kg")
    jumbo = D(quantity_packs) * D(2530) + D(4990)
    santa = D(quantity_packs) * D(2570) + D(3990)
    return {
        "packs": quantity_packs,
        "quantity_kg": "1.000000",
        "jumbo_total_clp": str(jumbo),
        "santa_total_clp": str(santa),
        "saving_clp": str(jumbo - santa),
        "saving_percent": str(100 * (jumbo - santa) / jumbo),
        "five_percent_met_in_reference_case": santa <= jumbo * D(".95"),
        "commercial_status": "REFERENCIA_SIMULADA_SIN_CHECKOUT_CONFIRMADO",
    }


def book_cost(data: dict, result: dict) -> dict:
    """Independently replay FEFO and reconcile every day with its physical simulation."""
    initial_qty = D(data["items"][0]["initial_quantity"])
    initial_cost = D(data["orders"][0]["total_clp"])
    closed = datetime.fromisoformat(data["lots"][0]["closed_expires_at"])
    lots = [ValuedLot(initial_qty, closed, closed, initial_cost / initial_qty, 0)]
    pending = list(result["orders"])
    weights = plan_weights(data)
    expenses = consumed_cost = waste_cost = D(0)
    number = 0

    def expire(at: datetime) -> Decimal:
        nonlocal waste_cost
        quantity = D(0)
        for lot in lots:
            if lot.quantity and lot.expires <= at:
                quantity += lot.quantity
                waste_cost += lot.quantity * lot.unit_book_cost
                lot.quantity = D(0)
        return quantity

    for row in result["daily"]:
        day = date.fromisoformat(row["date"])
        day_waste = expire(moment(day, 7, 30))
        for order in list(pending):
            if order["arrival"] != row["date"]:
                continue
            for i in range(order["packs"]):
                amount = (
                    piece_weight(date.fromisoformat(order["issued"]), i, weights)
                    if order["supplier"] == "Central Mayorista"
                    else D(order["nominal_kg"]) / order["packs"]
                )
                pack_cost = D(order["subtotal_clp"]) / order["packs"]
                number += 1
                closed = moment(day + timedelta(days=68), 0)
                lots.append(ValuedLot(amount, closed, closed, pack_cost / amount, number))
            pending.remove(order)
        day_waste += expire(moment(day, 18))
        needed = D(row["fulfilled_kg"])
        for lot in sorted(lots, key=lambda x: (x.expires, x.number)):
            if not needed:
                break
            used = min(needed, lot.quantity)
            if used and not lot.opened:
                lot.opened = True
                lot.expires = min(lot.closed_expires, moment(day, 18) + timedelta(days=5))
            consumed_cost += used * lot.unit_book_cost
            lot.quantity -= used
            needed -= used
        if needed or abs(sum((lot.quantity for lot in lots), D(0)) - D(row["closing_kg"])) > D(
            ".000001"
        ):
            raise ValueError("La valoración no reproduce los saldos físicos")
        if day_waste != D(row["waste_kg"]):
            raise ValueError("La valoración no reproduce los vencimientos físicos")
    expenses = sum((D(order["shipping_clp"]) for order in result["orders"]), D(0))
    final_value = sum((lot.quantity * lot.unit_book_cost for lot in lots), D(0))
    transit_value = sum((D(order["subtotal_clp"]) for order in pending), D(0))
    paid = D(result["metrics"]["purchases_clp"])
    incurred = initial_cost + paid - final_value - transit_value
    if abs(incurred - consumed_cost - waste_cost - expenses) > D(".000001"):
        raise ValueError("No concilia el libro de costos con compras y existencias")
    fulfilled = D(result["metrics"]["fulfilled_kg"])
    return {
        "opening_book_cost_clp": str(initial_cost),
        "paid_orders_clp": str(paid),
        "ending_inventory_book_value_clp": str(final_value),
        "in_transit_product_book_value_clp": str(transit_value),
        "fulfilled_product_cost_clp": str(consumed_cost),
        "expired_product_cost_clp": str(waste_cost),
        "dispatch_expense_clp": str(expenses),
        "attributable_cost_clp": str(incurred),
        "fulfilled_kg": str(fulfilled),
        "unmet_kg": result["metrics"]["unmet_kg"],
        "cost_per_fulfilled_kg": str(incurred / fulfilled) if fulfilled else None,
        "note": (
            "Valor contable por costo de adquisición, no precio de venta o rescate; "
            "eficiencia descriptiva, no servicio equivalente"
        ),
    }


def close_evaluation() -> dict:
    data, suppliers, digest = load_inputs()
    demand = build_demand(data, D("1.30"))
    manual = run_policy(data, suppliers, demand, "manual")
    current = run_policy(data, suppliers, demand, "stocksmart")
    candidate = run_candidate(
        data, suppliers, demand, Policy("precio_minimo_b1_c5", D(1), D(5), "precio_minimo")
    )
    valuation = {
        "manual": book_cost(data, manual),
        "stocksmart_existing": book_cost(data, current),
        "candidate": book_cost(data, candidate),
    }
    c_old, c_new = valuation["stocksmart_existing"], valuation["candidate"]
    if c_old["fulfilled_kg"] != c_new["fulfilled_kg"] or c_old["unmet_kg"] != c_new["unmet_kg"]:
        raise ValueError("No se puede comparar costo total con distinto servicio")
    increase = 100 * (D(c_new["attributable_cost_clp"]) / D(c_old["attributable_cost_clp"]) - 1)
    return {
        "dataset_sha256": digest,
        "decision_date": "2026-11-16",
        "context": {
            "commune": "Macul",
            "basket": "SOLO_PECHUGA",
            "mode": "DESPACHO_REFERENCE_SIMULATED",
            "accepted_scenarios": ["original", "estres_demanda_130"],
            "retail_minimum_assumed_clp": "19990",
            "retail_max_fiambre_kg": "1",
            "supplier_reference": "Jumbo",
            "common_delivery_window_assumed_days": 2,
            "historical_price_basis": "2026-10-07",
            "actual_checkout_or_b2b_contract": False,
        },
        "equal_quantity_equal_stock_reference_order": equal_order(),
        "inventory_valuation": valuation,
        "same_fulfilled_service_comparison": {
            "reference": "stocksmart_existing",
            "candidate": "precio_minimo_b1_c5",
            "fulfilled_kg_both": c_new["fulfilled_kg"],
            "inventory_accounting": "Existencias finales a costo de adquisición, no valor de venta",
            "candidate_cost_increase_percent": str(increase),
            "five_percent_saving_met": False,
        },
        "decisions": {
            "candidate_adoption": "NO_ADOPTAR_EN_PRODUCTO",
            "five_percent": "NO_CUMPLE_EN_REFERENCIA_COMPARABLE_Y_NO_ACREDITADO_REAL",
            "validation": "COMPLETADA_SINTETICA_CON_LIMITES_DE_ROBUSTEZ",
            "deliverable": "EVALUACION_CERRADA_RESULTADO_DESFAVORABLE_NO_DECLARAR_30_20_5",
        },
        "warning": (
            "Comuna y modalidad son decisiones del modelo; no prueban elegibilidad, stock, "
            "mínimos o aceptación de pedidos comerciales por los supermercados."
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=HERE / "resultados_locales" / "cierre_evaluacion.json"
    )
    args = parser.parse_args()
    result = close_evaluation()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
