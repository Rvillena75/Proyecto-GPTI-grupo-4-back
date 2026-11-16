"""Regressions for the new exploratory policies; frozen comparison stays untouched."""

import copy
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comparar_politicas import build_demand, load_inputs, run_policy  # noqa: E402
from explorar_politicas import (  # noqa: E402
    Policy,
    candidates,
    experiment,
    run_candidate,
    summarize,
)

D = Decimal
HERE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def inputs():
    data, suppliers, _ = load_inputs()
    return data, suppliers


def test_frozen_case_matches_previous_published_evidence(inputs):
    data, suppliers = inputs
    old = json.loads(
        (HERE / "evidencia/comparacion_politicas_2026-11-15.json").read_text(encoding="utf8")
    )
    for factor, name in ((D(1), "original"), (D("1.30"), "estres_demanda_130")):
        demand = build_demand(data, factor)
        for policy in ("manual", "stocksmart"):
            assert run_policy(data, suppliers, demand, policy) == old["scenarios"][name][policy]


def test_experiment_paired_demand_mass_and_input_immutable(inputs):
    data, suppliers = inputs
    saved = copy.deepcopy(data)
    demand = build_demand(data, D("1.30"))
    policy = Policy("candidate", D(1), D(5), "precio_minimo")
    result = run_candidate(data, suppliers, demand, policy)
    assert data == saved
    assert [D(x["demand_kg"]) for x in result["daily"]] == [x["demand"] for x in demand]
    m = result["metrics"]
    assert D(m["initial_kg"]) + D(m["received_kg"]) == D(m["fulfilled_kg"]) + D(
        m["expiry_waste_kg"]
    ) + D(m["closing_stock_kg"])
    assert D(m["demand_kg"]) == D(m["fulfilled_kg"]) + D(m["unmet_kg"])
    assert all(D(x["closing_kg"]) >= 0 for x in result["daily"])


def test_reproducible_exploratory_candidate_and_price_caveat(inputs):
    data, suppliers = inputs
    demand = build_demand(data, D("1.30"))
    policy = Policy("precio_minimo_b1_c5", D(1), D(5), "precio_minimo")
    result = run_candidate(data, suppliers, demand, policy)
    assert result == run_candidate(data, suppliers, demand, policy)
    assert result["metrics"]["closing_zero_days"] == 0
    assert D(result["metrics"]["unmet_kg"]) == 0
    assert D(result["metrics"]["expiry_waste_kg"]) == D("0.304")
    assert D(result["supplier_price_proxy"]["relative_percent"]) == D("7.055400")
    assert result["supplier_price_proxy"]["status"] == "PROXY_NO_VALIDADO_COMERCIALMENTE"
    assert D(result["metrics"]["purchases_clp"]) == D("875250")


def test_future_demand_does_not_change_past_decisions(inputs):
    data, suppliers = inputs
    demand = build_demand(data, D("1.30"))
    other = copy.deepcopy(demand)
    for row in other[40:]:
        row["demand"] *= 2
    policy = Policy("candidate", D(1), D(5), "precio_minimo")
    a = run_candidate(data, suppliers, demand, policy)
    b = run_candidate(data, suppliers, other, policy)
    assert a["daily"][:40] == b["daily"][:40]
    cutoff = demand[39]["date"]
    assert [o for o in a["orders"] if o["issued"] <= cutoff] == [
        o for o in b["orders"] if o["issued"] <= cutoff
    ]


def test_order_totals_and_no_baseline_zero_claim(inputs):
    data, suppliers = inputs
    demand = build_demand(data, D(1))
    result = run_candidate(
        data, suppliers, demand, Policy("candidate", D(1), D(5), "precio_minimo")
    )
    for order in result["orders"]:
        assert D(order["total_clp"]) == D(order["subtotal_clp"]) + D(order["shipping_clp"])
    scenario = experiment()["scenarios"]["1"]
    assert scenario["manual"]["metrics"]["closing_zero_days"] == 0
    assert all(
        x["targets"]["zero_days"]["relative_percent"] is None for x in scenario["alternatives"]
    )


def test_search_set_frozen_and_invalid_policy_rejected(inputs):
    data, suppliers = inputs
    assert len(candidates()) == 24
    with pytest.raises(ValueError):
        run_candidate(
            data,
            suppliers,
            build_demand(data, D(1)),
            Policy("invalid", D(2), D(2), "precio_minimo"),
        )


def test_published_summary_reproduces_canonical_results():
    evidence = json.loads(
        (HERE / "evidencia/resumen_exploracion_merma_2026-11-15.json").read_text(encoding="utf8")
    )
    assert summarize(experiment()) == evidence
