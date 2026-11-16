"""Exploratory, perishable-aware E3 policies; frozen baseline remains unchanged."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

from comparar_politicas import (
    HERE,
    Lot,
    build_demand,
    calculate_consumption,
    decimal_text,
    load_inputs,
    moment,
    piece_weight,
    plan_weights,
    relative_reduction,
    run_policy,
    total,
)
from scraper.pipeline import compare_offers

D = Decimal
SCALE = D("0.000001")


@dataclass(frozen=True)
class Policy:
    name: str
    buffer_days: Decimal
    coverage_days: Decimal
    strategy: str


def candidates() -> list[Policy]:
    # Declare the entire search space explicitly; any selection is post hoc exploration.
    return [
        Policy(f"{strategy}_b{b}_c{c}", D(b), D(c), strategy)
        for strategy in ("precio_minimo", "formato_pequeno")
        for b in (1, 2, 3)
        for c in (2, 3, 4, 5, 6)
        if c > b
    ]


def run_candidate(data: dict, suppliers: list[dict], demand: list[dict], policy: Policy) -> dict:
    if policy.strategy not in {"precio_minimo", "formato_pequeno"}:
        raise ValueError("Estrategia no reconocida")
    if not (D(0) < policy.buffer_days < policy.coverage_days):
        raise ValueError("Cobertura debe superar reserva")
    first = data["lots"][0]
    opening = D(data["items"][0]["initial_quantity"])
    closed = datetime.fromisoformat(first["closed_expires_at"])
    lots = [Lot(opening, closed, closed, 0)]
    weights = plan_weights(data)
    eligible = [
        s
        for s in suppliers
        if s["is_active"]
        and (policy.strategy == "precio_minimo" or not s["pack"].get("is_variable_weight"))
    ]
    if len(eligible) < 2:
        raise ValueError("El modelo requiere al menos dos opciones de proveedor")
    by_id = {s["supplier_id"]: s for s in eligible}
    prices = [q for q in data["price_quotes"] if q["supplier_id"] in by_id]
    lead = max(s["lead_time_days"] for s in eligible)
    observation = datetime.fromisoformat(data["items"][0]["observation_started_at"])
    pending, orders, daily, exits = [], [], [], []
    consumed = waste = unfilled = received_total = paid = D(0)
    number = 0

    def expire(at: datetime) -> Decimal:
        result = D(0)
        for lot in lots:
            if lot.remaining and lot.expires <= at:
                result += lot.remaining
                lot.remaining = D(0)
        return result

    for dayrow in demand:
        day = date.fromisoformat(dayrow["date"])
        opening_day = total(lots)
        today_waste = expire(moment(day, 7, 30))
        received = D(0)
        for order in list(pending):
            if order["arrival"] != day.isoformat():
                continue
            src = by_id[order["supplier_id"]]
            for i in range(order["packs"]):
                quantity = (
                    piece_weight(date.fromisoformat(order["issued"]), i, weights)
                    if src["pack"].get("is_variable_weight")
                    else D(order["nominal_kg"]) / order["packs"]
                )
                received += quantity
                number += 1
                closed = moment(day + timedelta(days=68), 0)
                lots.append(Lot(quantity, closed, closed, number))
            pending.remove(order)
        received_total += received
        today_waste += expire(moment(day, 18))
        needed = dayrow["demand"]
        served = min(needed, total(lots))
        remaining = served
        for lot in sorted(lots, key=lambda v: (v.expires, v.number)):
            if not remaining:
                break
            take = min(lot.remaining, remaining)
            if take and not lot.opened:
                lot.opened = True
                lot.expires = min(lot.closed_expires, moment(day, 18) + timedelta(days=5))
            lot.remaining -= take
            remaining -= take
        if remaining:
            raise ValueError("Diferencia entre stock y lotes")
        consumed += served
        unfilled += needed - served
        waste += today_waste
        if served:
            exits.append((moment(day, 18), served))

        now = moment(day, 21)
        window = calculate_consumption(observation, now, "America/Santiago", exits)
        average = window.weekly_average / D(7) if window.weekly_average is not None else None
        if average is not None and average > 0 and not pending:
            arrival = moment(day + timedelta(days=lead), 18)
            available_at_arrival = sum(
                (lot.remaining for lot in lots if lot.expires > arrival), D(0)
            )
            projected = max(D(0), available_at_arrival - average * lead)
            if projected <= average * policy.buffer_days:
                target = (average * policy.coverage_days - projected).quantize(
                    SCALE, rounding=ROUND_CEILING
                )
                options = compare_offers(prices, eligible, target)
                best = options["recommended"]
                if best is None:
                    raise ValueError("No hay opciones comparables")
                src = by_id[best["supplier_id"]]
                order = {
                    "issued": day.isoformat(),
                    "arrival": (day + timedelta(days=src["lead_time_days"])).isoformat(),
                    "supplier_id": src["supplier_id"],
                    "supplier": src["name"],
                    "packs": best["packs"],
                    "nominal_kg": best["purchased_quantity"],
                    "subtotal_clp": best["subtotal_clp"],
                    "shipping_clp": best["shipping_cost_clp"],
                    "total_clp": best["total_clp"],
                    "alternative_supplier_total_clp": options["next_best_total_clp"],
                }
                orders.append(order)
                pending.append(order)
                paid += D(order["total_clp"])
        closing = total(lots)
        if opening + received_total != consumed + waste + closing or closing < 0:
            raise ValueError("Error de balance de masa")
        daily.append(
            {
                "date": day.isoformat(),
                "opening_kg": decimal_text(opening_day),
                "received_kg": decimal_text(received),
                "demand_kg": decimal_text(needed),
                "fulfilled_kg": decimal_text(served),
                "unmet_kg": decimal_text(needed - served),
                "waste_kg": decimal_text(today_waste),
                "closing_kg": decimal_text(closing),
            }
        )
    alternative = sum((D(o["alternative_supplier_total_clp"]) for o in orders), D(0))
    proxy = relative_reduction(alternative, paid, D(5))
    if orders:
        proxy["status"] = "PROXY_NO_VALIDADO_COMERCIALMENTE"
    return {
        "policy": policy.name,
        "parameters": {
            "buffer_days": str(policy.buffer_days),
            "coverage_days": str(policy.coverage_days),
            "strategy": policy.strategy,
            "arrival_lead_days": lead,
        },
        "daily": daily,
        "orders": orders,
        "metrics": {
            "closing_zero_days": sum(D(r["closing_kg"]) == 0 for r in daily),
            "unmet_days": sum(D(r["unmet_kg"]) > 0 for r in daily),
            "unmet_kg": decimal_text(unfilled),
            "fulfilled_kg": decimal_text(consumed),
            "demand_kg": decimal_text(sum((x["demand"] for x in demand), D(0))),
            "expiry_waste_kg": decimal_text(waste),
            "initial_kg": decimal_text(opening),
            "received_kg": decimal_text(received_total),
            "closing_stock_kg": decimal_text(closing),
            "purchases_clp": decimal_text(paid),
            "orders_count": len(orders),
            "orders_in_transit": len(pending),
            "in_transit_nominal_kg": decimal_text(sum((D(o["nominal_kg"]) for o in pending), D(0))),
        },
        "supplier_price_proxy": proxy,
    }


def experiment() -> dict:
    data, suppliers, dataset_hash = load_inputs()
    scenarios = {}
    for factor in (D("1"), D("1.30"), D("1.15"), D("1.45")):
        demand = build_demand(data, factor)
        manual = run_policy(data, suppliers, demand, "manual")
        current = run_policy(data, suppliers, demand, "stocksmart")
        alternatives = []
        for policy in candidates():
            result = run_candidate(data, suppliers, demand, policy)
            base, metrics = manual["metrics"], result["metrics"]
            result["targets"] = {
                "zero_days": relative_reduction(
                    D(base["closing_zero_days"]), D(metrics["closing_zero_days"]), D(30)
                ),
                "waste_kg": relative_reduction(
                    D(base["expiry_waste_kg"]), D(metrics["expiry_waste_kg"]), D(20)
                ),
                "supplier_proxy": result["supplier_price_proxy"],
                "cash_outlay": {
                    "manual_clp": base["purchases_clp"],
                    "policy_clp": metrics["purchases_clp"],
                    "status": "NO_COMPARABLE_SERVICIO_E_INVENTARIO",
                },
            }
            alternatives.append(result)
        scenarios[str(factor)] = {
            "manual": manual,
            "stocksmart_existing": current,
            "alternatives": alternatives,
        }
    return {
        "dataset_sha256": dataset_hash,
        "base_frozen_commit": "aef2a42d2e4ba608c5c7ee5a71777b6edb05895e",
        "status": "EXPLORATORIO_POSTERIOR_A_RESULTADOS_SIN_VALIDACION_COMERCIAL",
        "disclosure": (
            "Grilla y escenarios seleccionados tras observar el caso anterior; no constituyen "
            "validación independiente. Políticas son propuestas, "
            "no funcionalidad actual de StockSmart."
        ),
        "assumptions": [
            "igual demanda por escenario",
            "autorización humana inmediata simulada",
            "precios fijos postdatados",
            "sin verificar mínimos ni factibilidad",
            "FEFO, 5 días abierto, stock y pérdida reportados",
            "dos estrategias por 12 pares de cobertura y reserva",
            "sin deuda de demanda: ventas no atendidas se pierden",
        ],
        "scenarios": scenarios,
    }


def summarize(result: dict) -> dict:
    canonical = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    summary = {
        "full_result_canonical_sha256": hashlib.sha256(canonical.encode("utf8")).hexdigest(),
        "dataset_sha256": result["dataset_sha256"],
        "base_frozen_commit": result["base_frozen_commit"],
        "status": result["status"],
        "disclosure": result["disclosure"],
        "assumptions": result["assumptions"],
        "scenarios": {},
    }
    for factor, scenario in result["scenarios"].items():
        summary["scenarios"][factor] = {
            "manual_metrics": scenario["manual"]["metrics"],
            "stocksmart_existing_metrics": scenario["stocksmart_existing"]["metrics"],
            "alternatives": [
                {key: policy[key] for key in ("policy", "parameters", "metrics", "targets")}
                for policy in scenario["alternatives"]
            ],
            "selected_exploratory_policy_detail": next(
                policy
                for policy in scenario["alternatives"]
                if policy["policy"] == "precio_minimo_b1_c5"
            ),
        }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=HERE / "resultados_locales" / "exploracion_merma.json"
    )
    parser.add_argument(
        "--summary", type=Path, default=HERE / "resultados_locales" / "resumen_exploracion.json"
    )
    args = parser.parse_args()
    result = experiment()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(
        json.dumps(summarize(result), ensure_ascii=False, indent=2) + "\n", encoding="utf8"
    )
    for name, scenario in result["scenarios"].items():
        baseline = scenario["manual"]["metrics"]
        passed = []
        for alt in scenario["alternatives"]:
            m = alt["metrics"]
            target = alt["targets"]
            a = target["zero_days"]
            b = target["waste_kg"]
            c = target["supplier_proxy"]
            if (
                a["relative_percent"] is not None
                and D(a["relative_percent"]) >= 30
                and b["relative_percent"] is not None
                and D(b["relative_percent"]) >= 20
                and c["relative_percent"] is not None
                and D(c["relative_percent"]) >= 5
            ):
                passed.append(
                    (
                        alt["policy"],
                        m["closing_zero_days"],
                        m["expiry_waste_kg"],
                        m["unmet_kg"],
                        m["purchases_clp"],
                        c["relative_percent"],
                    )
                )
        print(
            "factor",
            name,
            "base",
            baseline["closing_zero_days"],
            baseline["expiry_waste_kg"],
            "cumplen umbrales numéricos; ahorro sin validar",
            len(passed),
            passed[:10],
        )
    print("Detalle:", args.output)


if __name__ == "__main__":
    main()
