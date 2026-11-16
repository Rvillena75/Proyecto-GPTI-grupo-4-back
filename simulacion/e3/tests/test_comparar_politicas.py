"""Independent regression checks for the synthetic E3 comparison."""

import copy
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comparar_politicas import (  # noqa: E402
    build_demand,
    load_inputs,
    relative_reduction,
    run_policy,
    scenario,
)

D = Decimal


@pytest.fixture(scope="module")
def inputs():
    data, suppliers, _ = load_inputs()
    return data, suppliers


def test_manual_reproduces_every_frozen_closing_and_waste(inputs):
    data, suppliers = inputs
    got = run_policy(data, suppliers, build_demand(data, D(1)), "manual")
    for actual, expected in zip(got["daily"], data["daily_stock"], strict=True):
        assert D(actual["closing_kg"]) == D(expected["closing_stock"])
        assert D(actual["waste_kg"]) == D(expected["expired_quantity"])
    assert got["metrics"]["orders_count"] == 14
    assert D(got["metrics"]["expiry_waste_kg"]) == D("5.207")
    assert got["metrics"]["closing_zero_days"] == 0


def test_paired_scenario_has_identical_external_demand_and_mass_balance(inputs):
    data, suppliers = inputs
    result = scenario(data, suppliers, D("1.30"))
    a, b = result["manual"], result["stocksmart"]
    assert [r["demand_kg"] for r in a["daily"]] == [r["demand_kg"] for r in b["daily"]]
    assert D(a["metrics"]["demand_kg"]) == D(b["metrics"]["demand_kg"])
    for result in (a, b):
        m = result["metrics"]
        assert D(m["initial_kg"]) + D(m["received_kg"]) == (
            D(m["fulfilled_kg"]) + D(m["expiry_waste_kg"]) + D(m["closing_stock_kg"])
        )
        assert all(D(r["closing_kg"]) >= 0 for r in result["daily"])
        assert D(m["demand_kg"]) == D(m["fulfilled_kg"]) + D(m["unmet_kg"])


def test_zero_baseline_is_not_divided_or_declared_met():
    assert relative_reduction(D(0), D(0), D(30))["status"] == "NO_EVALUABLE_BASE_CERO"
    assert relative_reduction(D(0), D(1), D(30))["relative_percent"] is None


def test_fixed_stress_and_full_dataset_reproduce(inputs):
    data, suppliers = inputs
    original = copy.deepcopy(data)
    stress = scenario(data, suppliers, D("1.30"))
    assert stress == scenario(data, suppliers, D("1.30"))
    assert data == original
    assert stress["manual"]["metrics"]["closing_zero_days"] == 13
    assert stress["stocksmart"]["metrics"]["closing_zero_days"] == 0
    assert D(stress["targets"]["quiebres_dias_stock_cierre_cero"]["relative_percent"]) == 100
    assert stress["targets"]["merma_kg"]["status"] == "NO_CUMPLE_SIMULACION"
    assert (
        stress["targets"]["ahorro_proveedores_proxy"]["status"]
        == "PROXY_NO_VALIDADO_COMERCIALMENTE"
    )


def test_future_demand_changes_do_not_change_previous_decisions(inputs):
    data, suppliers = inputs
    demand = build_demand(data, D(1))
    changed = copy.deepcopy(demand)
    for day in changed[40:]:
        day["demand"] *= 2
    first = run_policy(data, suppliers, demand, "stocksmart")
    second = run_policy(data, suppliers, changed, "stocksmart")
    assert first["daily"][:40] == second["daily"][:40]
    cutoff = demand[39]["date"]
    assert [o for o in first["orders"] if o["issued"] <= cutoff] == [
        o for o in second["orders"] if o["issued"] <= cutoff
    ]


def test_shipping_is_once_per_order_and_no_equal_policy_advantage(inputs):
    data, suppliers = inputs
    demand = build_demand(data, D("1.30"))
    first = run_policy(data, suppliers, demand, "stocksmart")
    second = run_policy(data, suppliers, demand, "stocksmart")
    assert first == second
    assert (
        relative_reduction(
            D(first["metrics"]["purchases_clp"]), D(second["metrics"]["purchases_clp"]), D(5)
        )["relative_percent"]
        == "0.000000"
    )
    costs = {s["name"]: D(s["shipping_cost_clp"]) for s in suppliers}
    for order in first["orders"]:
        assert D(order["shipping_clp"]) == costs[order["supplier"]]
        assert D(order["total_clp"]) == D(order["subtotal_clp"]) + costs[order["supplier"]]
