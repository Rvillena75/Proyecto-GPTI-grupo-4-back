"""Validation of the frozen exploratory candidate, including commercial exclusions."""

import argparse
import json
import random
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.e2.demand import recipe_demand
from comparar_politicas import HERE, build_demand, load_inputs, run_policy
from explorar_politicas import Policy, run_candidate

D = Decimal
SEEDS = tuple(range(2026111600, 2026111630))
FIXED = Policy("precio_minimo_b1_c5", D(1), D(5), "precio_minimo")


def order_screen(order: dict) -> dict:
    """Screen of standalone online delivery, not a confirmation of checkout eligibility."""
    name = order["supplier"]
    if name not in {"Central Mayorista", "Jumbo", "Santa Isabel"}:
        raise ValueError("Proveedor sin condiciones revisadas")
    qty, subtotal = D(order["nominal_kg"]), D(order["subtotal_clp"])
    issues = []
    if name == "Central Mayorista":
        # Official parent-company publication is Temuco-specific, not an RM quotation.
        issues.append("MINIMO_CANAL_Y_PRECIO_NO_CONFIRMADOS_PARA_RM")
        if subtotal < 300000:
            issues.append("BAJO_REFERENCIA_PUBLICADA_300000_DESPACHO_TEMUCO")
        issues.append("DESPACHO_3990_ES_PROXY_NO_TARIFA_CENTRAL")
    else:
        if qty > 1:
            issues.append("SUPERA_LIMITE_PUBLICADO_1KG_QUESOS_FIAMBRES")
        minimum = 19990
        if subtotal < minimum:
            issues.append("BAJO_MINIMO_PUBLICADO_19990")
        if name == "Jumbo" and subtotal < 50000:
            issues.append("MINIMO_SEGUN_LOCALIDAD_19990_A_50000_SIN_CONFIRMAR")
    issues.append("SIN_CHECKOUT_DIRECCION_STOCK_Y_FECHA_CONFIRMADOS")
    return {
        "issued": order["issued"],
        "supplier": name,
        "quantity_kg": str(qty),
        "subtotal_clp": str(subtotal),
        "issues": issues,
        "eligible_verified": False,
    }


def adjusted_cost(metrics: dict, residual_value: Decimal, shortage_penalty: Decimal) -> Decimal:
    """Valuation sensitivity, not an emergency purchase or actual accounting valuation."""
    if residual_value < 0 or shortage_penalty < 0:
        raise ValueError("Los valores monetarios deben ser no negativos")
    inventory = D(metrics["closing_stock_kg"]) + D(metrics["in_transit_nominal_kg"])
    return (
        D(metrics["purchases_clp"])
        - residual_value * inventory
        + shortage_penalty * D(metrics["unmet_kg"])
    )


def penalty_threshold(base: dict, candidate: dict, residual_value: Decimal) -> dict:
    """Solve C_candidate <= .95*C_manual with the same unknown v and p."""
    b_inventory = D(base["closing_stock_kg"]) + D(base["in_transit_nominal_kg"])
    c_inventory = D(candidate["closing_stock_kg"]) + D(candidate["in_transit_nominal_kg"])
    denominator = D(".95") * D(base["unmet_kg"]) - D(candidate["unmet_kg"])
    numerator = (
        D(candidate["purchases_clp"])
        - D(".95") * D(base["purchases_clp"])
        - residual_value * (c_inventory - D(".95") * b_inventory)
    )
    return {
        "residual_value_clp_per_kg": str(residual_value),
        "minimum_penalty_clp_per_unmet_kg": str(max(D(0), numerator / denominator))
        if denominator > 0
        else None,
        "status": "SENSIBILIDAD_NO_COSTO_OBSERVADO",
    }


def new_demand(data: dict, seed: int, factor: Decimal) -> list[dict]:
    rng = random.Random(seed)
    recipes = [
        {
            "name": "sandwich_pechuga_asada",
            "customer_model": "POISSON",
            "weekday_servings": [float(D(x) * factor) for x in (5, 5, 6, 6, 7, 8, 5)],
            "quantity_per_serving": "75",
            "unit": "g",
        }
    ]
    result = []
    for row in data["demand"]:
        day = date.fromisoformat(row["date"])
        quantity = sum(
            (r["demand_quantity"] for r in recipe_demand(recipes, day.weekday(), rng, "kg")), D(0)
        )
        result.append({"date": row["date"], "demand": quantity})
    return result


def validation() -> dict:
    data, suppliers, digest = load_inputs()
    demand = build_demand(data, D("1.30"))
    baseline = run_policy(data, suppliers, demand, "manual")
    candidate = run_candidate(data, suppliers, demand, FIXED)
    trials = []
    for factor in (D(1), D("1.30")):
        for seed in SEEDS:
            demand = new_demand(data, seed, factor)
            b = run_policy(data, suppliers, demand, "manual")["metrics"]
            c = run_candidate(data, suppliers, demand, FIXED)["metrics"]
            trials.append(
                {
                    "factor": str(factor),
                    "seed": seed,
                    "manual": b,
                    "candidate": c,
                    "physical_thresholds_met": b["closing_zero_days"] > 0
                    and D(c["closing_zero_days"]) <= D(".7") * b["closing_zero_days"]
                    and D(c["expiry_waste_kg"]) <= D(".8") * D(b["expiry_waste_kg"]),
                }
            )
    aggregates = {}
    for factor in ("1", "1.30"):
        subset = [x for x in trials if x["factor"] == factor]

        def sums(policy, key, rows=subset):
            return sum((D(x[policy][key]) for x in rows), D(0))

        zb, zc = sums("manual", "closing_zero_days"), sums("candidate", "closing_zero_days")
        wb, wc = sums("manual", "expiry_waste_kg"), sums("candidate", "expiry_waste_kg")
        aggregates[factor] = {
            "trials": len(subset),
            "manual_zero_days_sum": str(zb),
            "candidate_zero_days_sum": str(zc),
            "zero_day_reduction_percent": str(100 * (zb - zc) / zb) if zb else None,
            "manual_waste_kg_sum": str(wb),
            "candidate_waste_kg_sum": str(wc),
            "waste_reduction_percent": str(100 * (wb - wc) / wb) if wb else None,
            "trials_physical_thresholds_met": sum(x["physical_thresholds_met"] for x in subset),
        }
    return {
        "candidate_frozen": FIXED.name,
        "seeds_declared_before_run": list(SEEDS),
        "dataset_sha256": digest,
        "candidate_retuned": False,
        "scope": "NUEVAS_REALIZACIONES_SINTETICAS_DEL_MISMO_MODELO_NO_DATOS_REALES",
        "commercial_status": "AHORRO_5_NO_ACREDITADO_PEDIDOS_INCOMPATIBLES_O_SIN_CONFIRMAR",
        "commercial_screen": [order_screen(o) for o in candidate["orders"]],
        "cost_definition": (
            "C(v,p)=desembolso-v*(stock_final+transito_nominal)+p*demanda_no_atendida; "
            "v y p comunes a ambas políticas, sin valores reales confirmados"
        ),
        "economic_warning": (
            "Es una sensibilidad contable; agregar una penalización no demuestra que se pueda "
            "atender la demanda perdida. El precio de rescate e inventario debe documentarse "
            "antes de usarlo para acreditar ahorro."
        ),
        "cost_sensitivity": [
            penalty_threshold(baseline["metrics"], candidate["metrics"], v)
            for v in (D(0), D(13550))
        ],
        "price_review": {
            "Jumbo": (
                "2530 CLP por 125 g visible en página oficial; "
                "sin confirmar checkout ni fecha histórica"
            ),
            "Santa Isabel": "página requiere JavaScript; no se confirmó precio actual",
            "Central Mayorista": (
                "página de producto no accesible en la revisión; precio actual no confirmado"
            ),
        },
        "aggregates": aggregates,
        "trials": trials,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=HERE / "resultados_locales" / "validacion_candidata.json"
    )
    args = parser.parse_args()
    result = validation()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(
        json.dumps(
            {
                "commercial_status": result["commercial_status"],
                "aggregates": result["aggregates"],
                "cost_sensitivity": result["cost_sensitivity"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
