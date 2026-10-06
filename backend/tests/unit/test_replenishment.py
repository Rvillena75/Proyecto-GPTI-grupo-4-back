"""C1/C5/C12 and orders in transit, with the numbers of Sofi's mockup."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from app.domain.forecast import Consumption, assess_stock

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
# Mockup: 7 u per week (1 u/day), safe stock 5 u, stock 9 u, suppliers with 2, 3 and 4 days.
WEEKLY_7 = Consumption("ESTABLISHED", 28, D(28), D(7), None, None)


def test_mockup_alerts_today_with_the_longest_lead_time() -> None:
    result = assess_stock(D(9), D(5), WEEKLY_7, NOW, lead_time_days=4)
    assert result.projected_stock_at_arrival == D(5)
    assert result.alert_status == "LOW_STOCK"  # 5 <= 5: the edge alerts (C1, "<=")
    assert result.suggested_quantity == D(9)  # 2 weeks (14) - 5 at arrival (C5)
    assert result.order_by_at == NOW  # order today
    assert result.lead_time_days == 4


def test_shorter_lead_time_does_not_alert_yet() -> None:
    result = assess_stock(D(9), D(5), WEEKLY_7, NOW, lead_time_days=3)
    assert result.projected_stock_at_arrival == D(6)
    assert result.alert_status == "OK"
    assert result.is_low_stock is False
    assert result.order_by_at == NOW + timedelta(days=1)


def test_order_in_transit_attends_the_alert_and_is_discounted() -> None:
    partial = assess_stock(D(9), D(5), WEEKLY_7, NOW, lead_time_days=4, in_transit=D(4))
    assert partial.alert_status == "IN_TRANSIT"
    assert partial.is_low_stock is True
    assert partial.suggested_quantity == D(5)
    covered = assess_stock(D(9), D(5), WEEKLY_7, NOW, lead_time_days=4, in_transit=D(9))
    assert covered.suggested_quantity == D(0)
    assert covered.in_transit_quantity == D(9)


def test_projection_never_goes_below_zero_and_order_is_overdue() -> None:
    result = assess_stock(D(2), D(5), WEEKLY_7, NOW, lead_time_days=4)
    assert result.projected_stock_at_arrival == D(0)
    assert result.suggested_quantity == D(14)
    assert result.order_by_at == NOW - timedelta(days=7)  # should have ordered a week ago


def test_without_suppliers_the_old_p5_rule_still_holds() -> None:
    result = assess_stock(
        D(10), None, Consumption("ESTABLISHED", 28, D(56), D(14), None, None), NOW
    )
    assert result.lead_time_days is None
    assert result.projected_stock_at_arrival == D(10)
    assert result.alert_status == "LOW_STOCK"
    assert result.suggested_quantity == D(18)


def test_manual_threshold_without_history_ignores_lead_time() -> None:
    empty = Consumption("INSUFFICIENT_HISTORY", 0, D(0), None, None, None)
    result = assess_stock(D(7), D(10), empty, NOW, lead_time_days=4)
    assert result.alert_status == "LOW_STOCK"
    assert result.projected_stock_at_arrival is None
    assert result.suggested_quantity is None
    assert result.order_by_at is None
