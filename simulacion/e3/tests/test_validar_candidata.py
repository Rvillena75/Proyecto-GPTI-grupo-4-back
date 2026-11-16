import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comparar_politicas import load_inputs  # noqa: E402
from validar_candidata import (  # noqa: E402
    FIXED,
    SEEDS,
    adjusted_cost,
    new_demand,
    order_screen,
    penalty_threshold,
)

D = Decimal


def test_retail_quantity_cap_and_location_still_require_confirmation():
    order = {
        "supplier": "Jumbo",
        "issued": "2026-01-01",
        "nominal_kg": "1.125",
        "subtotal_clp": "22770",
    }
    result = order_screen(order)
    assert "SUPERA_LIMITE_PUBLICADO_1KG_QUESOS_FIAMBRES" in result["issues"]
    assert not result["eligible_verified"]
    order["nominal_kg"] = "1"
    assert "SUPERA_LIMITE_PUBLICADO_1KG_QUESOS_FIAMBRES" not in order_screen(order)["issues"]


def test_central_reference_not_treated_as_confirmed_rm_contract():
    result = order_screen(
        {
            "supplier": "Central Mayorista",
            "issued": "2026-01-01",
            "nominal_kg": "2.5",
            "subtotal_clp": "33875",
        }
    )
    assert "MINIMO_CANAL_Y_PRECIO_NO_CONFIRMADOS_PARA_RM" in result["issues"]
    assert not result["eligible_verified"]


def test_adjusted_cost_shared_values_and_penalty_boundary():
    b = {
        "purchases_clp": "100",
        "closing_stock_kg": "1",
        "in_transit_nominal_kg": "0",
        "unmet_kg": "2",
    }
    c = {
        "purchases_clp": "120",
        "closing_stock_kg": "2",
        "in_transit_nominal_kg": "0",
        "unmet_kg": "0",
    }
    v = D(5)
    boundary = penalty_threshold(b, c, v)
    p = D(boundary["minimum_penalty_clp_per_unmet_kg"])
    assert abs(adjusted_cost(c, v, p) - D(".95") * adjusted_cost(b, v, p)) < D(".000001")
    assert adjusted_cost(c, v, p + D(1)) < D(".95") * adjusted_cost(b, v, p + D(1))
    with pytest.raises(ValueError):
        adjusted_cost(c, D(-1), D(0))


def test_new_random_demands_reproducible_and_seed_set_fixed():
    data, _, _ = load_inputs()
    assert FIXED.buffer_days == 1 and FIXED.coverage_days == 5
    assert SEEDS == tuple(range(2026111600, 2026111630))
    first = new_demand(data, SEEDS[0], D("1.30"))
    assert first == new_demand(data, SEEDS[0], D("1.30"))
    assert first != new_demand(data, SEEDS[1], D("1.30"))
    assert len(first) == 70


def test_validation_records_all_new_trials_without_asserting_five_percent():
    p = Path(__file__).resolve().parents[1] / "evidencia" / "validacion_candidata.json"
    data = json.loads(p.read_text(encoding="utf8"))
    assert len(data["trials"]) == 60
    assert not data["candidate_retuned"]
    assert data["commercial_status"].startswith("AHORRO_5_NO_ACREDITADO")
    assert all(not x["eligible_verified"] for x in data["commercial_screen"])
    assert data["aggregates"]["1.30"]["trials_physical_thresholds_met"] == 18


def test_validation_is_reproducible_with_fixed_candidate():
    from validar_candidata import validation

    evidence = json.loads(
        (Path(__file__).resolve().parents[1] / "evidencia/validacion_candidata.json").read_text(
            encoding="utf8"
        )
    )
    assert validation() == evidence
