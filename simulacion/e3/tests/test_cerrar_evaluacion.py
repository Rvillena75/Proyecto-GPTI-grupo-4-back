"""Equal-service and inventory accounting checks for the final simulation decision."""

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cerrar_evaluacion import close_evaluation, equal_order  # noqa: E402

D = Decimal


def test_equal_order_compares_same_pack_and_does_not_invent_five_percent():
    result = equal_order()
    assert result["quantity_kg"] == "1.000000"
    assert D(result["jumbo_total_clp"]) == 25230
    assert D(result["santa_total_clp"]) == 24550
    assert D(result["saving_clp"]) == 680
    assert D(result["saving_percent"]) < 5
    assert not result["five_percent_met_in_reference_case"]
    with pytest.raises(ValueError):
        equal_order(9)


def test_book_values_reconcile_and_same_service_pair_cost_is_higher():
    result = close_evaluation()
    for v in result["inventory_valuation"].values():
        actual = (
            D(v["opening_book_cost_clp"])
            + D(v["paid_orders_clp"])
            - D(v["ending_inventory_book_value_clp"])
            - D(v["in_transit_product_book_value_clp"])
        )
        assert abs(actual - D(v["attributable_cost_clp"])) < D(".000001")
        assert abs(
            actual
            - D(v["fulfilled_product_cost_clp"])
            - D(v["expired_product_cost_clp"])
            - D(v["dispatch_expense_clp"])
        ) < D(".000001")
    candidate = result["inventory_valuation"]["candidate"]
    existing = result["inventory_valuation"]["stocksmart_existing"]
    assert candidate["fulfilled_kg"] == existing["fulfilled_kg"] == "41.775000"
    assert candidate["unmet_kg"] == existing["unmet_kg"] == "0.000000"
    assert D(candidate["ending_inventory_book_value_clp"]) == D("67567.6")
    assert D(candidate["attributable_cost_clp"]) == D("841557.4")
    assert D(candidate["attributable_cost_clp"]) > D(existing["attributable_cost_clp"])
    assert not result["same_fulfilled_service_comparison"]["five_percent_saving_met"]


def test_closed_decisions_are_explicit_simulation_only():
    result = close_evaluation()
    assert result["context"]["commune"] == "Macul"
    assert not result["context"]["actual_checkout_or_b2b_contract"]
    assert result["decisions"]["candidate_adoption"] == "NO_ADOPTAR_EN_PRODUCTO"


def test_final_evidence_reproduces():
    path = Path(__file__).resolve().parents[1] / "evidencia" / "cierre_evaluacion_2026-11-16.json"
    assert close_evaluation() == json.loads(path.read_text(encoding="utf8"))
