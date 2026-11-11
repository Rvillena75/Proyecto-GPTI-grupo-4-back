from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.domain.commercial import CommercialPack
from app.domain.errors import RuleViolation
from app.domain.forecast import Consumption, assess_stock
from app.domain.replenishment import (
    SupplierLeadTime,
    assess_replenishment,
    maximum_active_lead_time,
)


def test_formats_12_20_30_share_one_egg_base_unit() -> None:
    for count in (12, 20, 30):
        pack = CommercialPack("bandeja", D(1), "unidad", count, "unidad")
        assert pack.pack_quantity == count
        assert pack.quantity_for_packs(2) == count * 2
    dozen = CommercialPack("bandeja", D(1), "unidad", 12, "unidad")
    assert dozen.unit_price(D(4290)) == D("357.500000")
    assert dozen.packs_for(D(13)) == 2
    assert dozen.packs_for(D(0)) == 0
    liquid = CommercialPack("caja 6x200mL", D(200), "mL", 6, "L")
    assert liquid.pack_quantity == D("1.2")
    assert liquid.conversion_factor == D("0.001")
    assert liquid.unit_price(D(6000)) == D(5000)


@pytest.mark.parametrize(
    "quantity,unit,count,base",
    [
        (D(0), "unidad", 12, "unidad"),
        (D(1), "kg", 12, "unidad"),
        (D("0.5"), "unidad", 1, "unidad"),
        (D(1), "unidad", 0, "unidad"),
        (D(1), "unidad", True, "unidad"),
        (D("NaN"), "mL", 1, "L"),
    ],
)
def test_invalid_format_is_rejected(quantity, unit, count, base) -> None:
    with pytest.raises(RuleViolation):
        CommercialPack("pack", quantity, unit, count, base)


def test_pedro_equality_example_uses_maximum_active_calendar_lead() -> None:
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)  # Friday
    summary = Consumption("ESTABLISHED", 28, D(28), D(7), None, None)
    lead = maximum_active_lead_time(
        [
            SupplierLeadTime(1, 2),
            SupplierLeadTime(2, 3),
            SupplierLeadTime(3, 4),
            SupplierLeadTime(4, 99, False),
        ]
    )
    assert lead == 4
    exact = assess_replenishment(D(9), D(5), summary, now, lead_time_days=lead)
    assert exact.projected_arrival_stock == D(5)
    assert exact.is_low_stock is True
    assert exact.alert_status == "LOW_STOCK"
    assert exact.required_quantity == exact.suggested_quantity == D(9)
    assert assess_stock(D(9), D(5), summary, now).alert_status == "OK"  # No L/T in endpoint.
    assert (now + timedelta(days=lead)).weekday() == 1  # Tue, weekend counted.
    above = assess_replenishment(D("9.000001"), D(5), summary, now, lead_time_days=lead)
    assert above.is_low_stock is False
    transit = assess_replenishment(
        D(9), D(5), summary, now, lead_time_days=lead, in_transit_quantity=D(9)
    )
    assert transit.alert_status == "IN_TRANSIT"
    assert transit.required_quantity == transit.suggested_quantity == 0


def test_automatic_safe_stock_and_no_active_supplier_are_explicit() -> None:
    now = datetime(2026, 10, 5, tzinfo=UTC)
    summary = Consumption("ESTABLISHED", 28, D(28), D(7), None, None)
    exact = assess_replenishment(D(11), None, summary, now, lead_time_days=4)
    assert exact.safe_stock == exact.projected_arrival_stock == D(7)
    assert exact.is_low_stock is True
    with pytest.raises(RuleViolation, match="Configure an active supplier"):
        maximum_active_lead_time([SupplierLeadTime(1, 4, False)])
    with pytest.raises(RuleViolation):
        assess_replenishment(D(11), None, summary, now, lead_time_days=-1)


def test_insufficient_history_cannot_project_a_manual_threshold() -> None:
    now = datetime(2026, 10, 5, tzinfo=UTC)
    empty = Consumption("INSUFFICIENT_HISTORY", 0, D(0), None, None, None)
    result = assess_replenishment(D(9), D(5), empty, now, lead_time_days=4)
    assert result.is_low_stock is False  # Manual P5 compares current 9 > safe 5 without forecast.
    assert result.alert_status == "OK"
    assert result.projected_stock_at_arrival is None
    assert result.suggested_quantity is None
