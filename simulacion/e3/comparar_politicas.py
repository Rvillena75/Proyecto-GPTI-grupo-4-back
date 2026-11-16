"""Controlled E3 policy experiment over frozen synthetic inputs, not a production simulator."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "backend"))
from app.domain.forecast import calculate_consumption  # noqa: E402
from app.domain.replenishment import assess_replenishment  # noqa: E402
from scraper.pipeline import compare_offers, load_products  # noqa: E402

D = Decimal
ZONE = ZoneInfo("America/Santiago")
QTY_SCALE = D("0.000001")


@dataclass
class Lot:
    remaining: Decimal
    expires: datetime
    closed_expires: datetime
    number: int
    opened: bool = False


def moment(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), ZONE)


def decimal_text(value: Decimal) -> str:
    return str(value.quantize(QTY_SCALE))


def load_inputs() -> tuple[dict, list[dict], str]:
    folder = HERE / "dataset_v2"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        actual = hashlib.sha256((folder / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"El archivo congelado {name} no coincide con el manifiesto")
    source = folder / "dataset.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    return (
        data,
        load_products(HERE / "parametros_productos.json"),
        hashlib.sha256(source.read_bytes()).hexdigest(),
    )


def total(lots: list[Lot]) -> Decimal:
    return sum((lot.remaining for lot in lots), D(0))


def build_demand(data: dict, factor: Decimal) -> list[dict]:
    # Recalculate a whole number of synthetic sandwich servings, never a fraction of a customer.
    days = []
    for row in data["demand"]:
        if factor == 1:
            qty = D(row["demand_quantity"])
        else:
            qty = sum(
                (
                    (D(r["servings"]) * factor).to_integral_value(rounding=ROUND_HALF_UP)
                    * D(r["quantity_per_serving"])
                    for r in row["recipe_details"]
                ),
                D(0),
            )
        days.append({"date": row["date"], "demand": qty.quantize(QTY_SCALE)})
    return days


def plan_weights(data: dict) -> dict[date, list[Decimal]]:
    receipts = {r["order_id"]: r for r in data["receipts"]}
    return {
        date.fromisoformat(o["issued_at"][:10]): [
            D(x) for x in receipts[o["id"]]["piece_quantities"]
        ]
        for o in data["orders"]
        if not o["is_opening_balance"]
    }


def piece_weight(day: date, index: int, baseline: dict[date, list[Decimal]]) -> Decimal:
    if day in baseline and index < len(baseline[day]):
        return baseline[day][index]
    # Counterfactual draws keyed by order date and piece, independent of demand and policy.
    digest = hashlib.sha256(f"e3-fixed-weight-v1:{day.isoformat()}:{index}".encode()).digest()
    return D(2450 + int.from_bytes(digest[:4], "big") % 101) / D(1000)


def relative_reduction(base: Decimal, other: Decimal, threshold: Decimal) -> dict:
    if base == 0:
        return {
            "baseline": str(base),
            "policy": str(other),
            "relative_percent": None,
            "threshold_percent": str(threshold),
            "status": "NO_EVALUABLE_BASE_CERO",
        }
    value = (base - other) / base * D(100)
    return {
        "baseline": str(base),
        "policy": str(other),
        "relative_percent": decimal_text(value),
        "threshold_percent": str(threshold),
        "status": "CUMPLE_SIMULACION" if value >= threshold else "NO_CUMPLE_SIMULACION",
    }


def run_policy(data: dict, suppliers: list[dict], demand: list[dict], policy: str) -> dict:
    if policy not in {"manual", "stocksmart"}:
        raise ValueError("Política desconocida")
    first = data["lots"][0]
    opening = D(data["items"][0]["initial_quantity"])
    lots = [
        Lot(
            opening,
            datetime.fromisoformat(first["closed_expires_at"]),
            datetime.fromisoformat(first["closed_expires_at"]),
            0,
        )
    ]
    weights = plan_weights(data)
    supplier_by_id = {s["supplier_id"]: s for s in suppliers}
    quotes = data["price_quotes"]
    observation = datetime.fromisoformat(data["items"][0]["observation_started_at"])
    max_lead = max(s["lead_time_days"] for s in suppliers if s["is_active"])
    pending, orders, daily, exits = [], [], [], []
    used, waste, unfilled, purchased, cost = D(0), D(0), D(0), D(0), D(0)
    lot_counter = 0

    def make_order(
        day: date,
        supplier_id: int,
        packs: int,
        qty: Decimal,
        unit_cost: Decimal,
        shipping: Decimal,
        alternative: Decimal | None = None,
    ) -> None:
        nonlocal cost
        src = supplier_by_id[supplier_id]
        bill = unit_cost * packs + shipping
        order = {
            "issued": day.isoformat(),
            "arrival": (day + timedelta(days=src["lead_time_days"])).isoformat(),
            "supplier_id": supplier_id,
            "supplier": src["name"],
            "packs": packs,
            "nominal_kg": decimal_text(qty),
            "subtotal_clp": decimal_text(unit_cost * packs),
            "shipping_clp": decimal_text(shipping),
            "total_clp": decimal_text(bill),
            "alternative_supplier_total_clp": decimal_text(alternative)
            if alternative is not None
            else None,
        }
        orders.append(order)
        pending.append(order)
        cost += bill

    def expire(at: datetime) -> Decimal:
        expired = D(0)
        for lot in lots:
            if lot.remaining and lot.expires <= at:
                expired += lot.remaining
                lot.remaining = D(0)
        return expired

    for index, input_day in enumerate(demand):
        day = date.fromisoformat(input_day["date"])
        start_stock = total(lots)
        day_waste = expire(moment(day, 7, 30))
        received = D(0)
        for order in list(pending):
            if order["arrival"] != day.isoformat():
                continue
            src = supplier_by_id[order["supplier_id"]]
            for n in range(order["packs"]):
                qty = (
                    piece_weight(date.fromisoformat(order["issued"]), n, weights)
                    if src["pack"].get("is_variable_weight")
                    else D(order["nominal_kg"]) / order["packs"]
                )
                received += qty
                lot_counter += 1
                closed = moment(day + timedelta(days=68), 0)
                lots.append(Lot(qty, closed, closed, lot_counter))
            pending.remove(order)
        purchased += received
        if policy == "manual" and index % 5 == 0:
            src = supplier_by_id[1]
            quote = next(q for q in quotes if q["supplier_id"] == 1)
            make_order(day, 1, 1, D("2.5"), D(quote["pack_price_clp"]), D(src["shipping_cost_clp"]))
        day_waste += expire(moment(day, 18))
        wanted = input_day["demand"]
        served = min(total(lots), wanted)
        remaining = served
        for lot in sorted(lots, key=lambda x: (x.expires, x.number)):
            if remaining == 0:
                break
            take = min(lot.remaining, remaining)
            if take and not lot.opened:
                lot.opened = True
                lot.expires = min(lot.closed_expires, moment(day, 18) + timedelta(days=5))
            lot.remaining -= take
            remaining -= take
        if remaining != 0:
            raise ValueError("El stock no coincide con los lotes disponibles")
        used += served
        if served:
            exits.append((moment(day, 18), served))
        unmet = wanted - served
        unfilled += unmet
        waste += day_waste
        if policy == "stocksmart":
            at = moment(day, 21)
            consumption = calculate_consumption(observation, at, "America/Santiago", exits)
            transit = sum((D(o["nominal_kg"]) for o in pending), D(0))
            assessment = assess_replenishment(
                total(lots),
                None,
                consumption,
                at,
                lead_time_days=max_lead,
                in_transit_quantity=transit,
            )
            if assessment.alert_status == "LOW_STOCK" and assessment.suggested_quantity:
                quantity = assessment.suggested_quantity.quantize(QTY_SCALE, rounding=ROUND_CEILING)
                comparison = compare_offers(quotes, suppliers, quantity)
                if comparison["recommended"] is None:
                    raise ValueError("No hay suficientes proveedores comparables")
                best = comparison["recommended"]
                make_order(
                    day,
                    best["supplier_id"],
                    best["packs"],
                    D(best["purchased_quantity"]),
                    D(best["subtotal_clp"]) / best["packs"],
                    D(best["shipping_cost_clp"]),
                    D(comparison["next_best_total_clp"]),
                )
        end_stock = total(lots)
        if end_stock < 0 or opening + purchased != used + waste + end_stock:
            raise ValueError("Se incumplió la conservación de masa")
        daily.append(
            {
                "date": day.isoformat(),
                "opening_kg": decimal_text(start_stock),
                "received_kg": decimal_text(received),
                "demand_kg": decimal_text(wanted),
                "fulfilled_kg": decimal_text(served),
                "unmet_kg": decimal_text(unmet),
                "waste_kg": decimal_text(day_waste),
                "closing_kg": decimal_text(end_stock),
            }
        )
    return {
        "policy": policy,
        "daily": daily,
        "orders": orders,
        "metrics": {
            "closing_zero_days": sum(D(r["closing_kg"]) == 0 for r in daily),
            "unmet_days": sum(D(r["unmet_kg"]) > 0 for r in daily),
            "unmet_kg": decimal_text(unfilled),
            "demand_kg": decimal_text(sum((r["demand"] for r in demand), D(0))),
            "fulfilled_kg": decimal_text(used),
            "expiry_waste_kg": decimal_text(waste),
            "initial_kg": decimal_text(opening),
            "received_kg": decimal_text(purchased),
            "closing_stock_kg": decimal_text(total(lots)),
            "purchases_clp": decimal_text(cost),
            "orders_count": len(orders),
            "orders_in_transit": len(pending),
            "in_transit_nominal_kg": decimal_text(sum((D(o["nominal_kg"]) for o in pending), D(0))),
        },
    }


def scenario(data: dict, suppliers: list[dict], factor: Decimal) -> dict:
    demand = build_demand(data, factor)
    manual = run_policy(data, suppliers, demand, "manual")
    smart = run_policy(data, suppliers, demand, "stocksmart")
    b, s = manual["metrics"], smart["metrics"]
    comparable_orders = [
        order for order in smart["orders"] if order["alternative_supplier_total_clp"] is not None
    ]
    alternative_total = sum(
        (D(order["alternative_supplier_total_clp"]) for order in comparable_orders), D(0)
    )
    recommended_total = sum((D(order["total_clp"]) for order in comparable_orders), D(0))
    supplier_proxy = (
        relative_reduction(alternative_total, recommended_total, D(5))
        if comparable_orders
        else {"relative_percent": None, "status": "NO_EVALUABLE_SIN_ORDENES"}
    )
    if comparable_orders:
        supplier_proxy["status"] = "PROXY_NO_VALIDADO_COMERCIALMENTE"
    supplier_proxy["reason"] = (
        "Comparación de ofertas de la misma necesidad; "
        "factibilidad comercial y canasta no verificadas"
    )
    supplier_proxy["orders_compared"] = len(comparable_orders)
    return {
        "demand_factor": str(factor),
        "manual": manual,
        "stocksmart": smart,
        "targets": {
            "quiebres_dias_stock_cierre_cero": relative_reduction(
                D(b["closing_zero_days"]), D(s["closing_zero_days"]), D(30)
            ),
            "merma_kg": relative_reduction(D(b["expiry_waste_kg"]), D(s["expiry_waste_kg"]), D(20)),
            "ahorro_proveedores_proxy": supplier_proxy,
            "desembolso_ordenes_diferencia_descriptiva": {
                "manual_clp": b["purchases_clp"],
                "stocksmart_clp": s["purchases_clp"],
                "status": "NO_COMPARABLE_SERVICIO_E_INVENTARIO",
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=HERE / "resultados_locales" / "comparacion_politicas.json"
    )
    args = parser.parse_args()
    data, suppliers, digest = load_inputs()
    original = scenario(data, suppliers, D(1))
    # Replication is a prerequisite: preserve the 70 frozen daily balances and waste.
    for actual, expected in zip(original["manual"]["daily"], data["daily_stock"], strict=True):
        if D(actual["closing_kg"]) != D(expected["closing_stock"]) or D(actual["waste_kg"]) != D(
            expected["expired_quantity"]
        ):
            raise AssertionError(f"La política manual no reproduce Dataset v2: {actual['date']}")
    stress = scenario(data, suppliers, D("1.30"))
    result = {
        "dataset_sha256": digest,
        "origin": "SIMULACION_SINTETICA_NO_DATOS_REALES",
        "notice": (
            "Las condiciones comerciales y costos son supuestos; los precios del 07/10 son "
            "un proxy constante aplicado antes de su fecha observada. No representan precios "
            "históricos ni resultados causales reales."
        ),
        "stress_selection": (
            "Escenario adicional solicitado para permitir quiebres: demanda por recetas x1.30, "
            "redondeada a servicios enteros. Definido después de conocer que la base tiene "
            "cero quiebres; no es validación independiente."
        ),
        "assumptions": [
            "mismas demandas diarias en ambas políticas por escenario",
            "pedidos humanos aprobados inmediatamente",
            "compara formatos enteros sin validar mínimos ni factibilidad real",
            "sin shocks de retraso",
            "precios fijos postdatados como proxy contrafactual",
            "despacho una vez por pedido",
            "se reportan inventario final y pedidos pendientes; "
            "desembolso no ajustado por inventario",
            "costo de apertura anterior al horizonte excluido de ambas políticas",
        ],
        "scenarios": {"original": original, "estres_demanda_130": stress},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for name, item in result["scenarios"].items():
        print(
            name,
            json.dumps(
                {
                    "manual": item["manual"]["metrics"],
                    "stocksmart": item["stocksmart"]["metrics"],
                    "targets": item["targets"],
                },
                ensure_ascii=False,
            ),
        )
    print("Detalle:", args.output)


if __name__ == "__main__":
    main()
