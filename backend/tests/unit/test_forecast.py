from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from app.domain.forecast import assess_stock, calculate_consumption


def test_28_days_and_projection() -> None:
    now = datetime(2026, 9, 29, 12, tzinfo=UTC)
    start = datetime(2026, 8, 31, 3, tzinfo=UTC)  # 31 Aug 00:00 in Santiago
    exits = [(datetime(2026, 9, day, 12, tzinfo=UTC), D(2)) for day in range(1, 29)]
    result = calculate_consumption(start, now, "America/Santiago", exits)
    assert result.complete_days == 28
    assert result.observed_quantity == D(56)
    assert result.weekly_average == D(14)
    assessment = assess_stock(D(10), None, result, now)
    assert assessment.daily_average == D(2)
    assert assessment.remaining_days == D(5)
    assert assessment.depletion_at == now + timedelta(days=5)
    assert assessment.depletion_status == "PROJECTED"


def test_fractional_depletion_days_preserve_half_day() -> None:
    from app.domain.forecast import Consumption

    now = datetime(2026, 9, 29, 12, tzinfo=UTC)
    summary = Consumption("ESTABLISHED", 28, D(56), D(14), None, None)
    assessment = assess_stock(D(5), None, summary, now)
    assert assessment.remaining_days == D("2.5")
    assert assessment.depletion_at == now + timedelta(days=2, hours=12)


def test_four_full_days_are_preliminary_and_zero_is_observed() -> None:
    now = datetime(2026, 9, 15, 15, tzinfo=UTC)
    started = datetime(2026, 9, 10, 15, tzinfo=UTC)
    exits = [(datetime(2026, 9, 12, 15, tzinfo=UTC), D(8))]
    result = calculate_consumption(started, now, "America/Santiago", exits)
    assert result.complete_days == 4
    assert result.weekly_average == D(14)
    assert result.status == "PRELIMINARY"
    zero = calculate_consumption(started, now, "America/Santiago", [])
    assert zero.weekly_average == 0
    assert assess_stock(D(10), None, zero, now).depletion_at is None
    assert assess_stock(D(10), None, zero, now).depletion_status == "ZERO_CONSUMPTION"


def test_missing_history_and_manual_threshold() -> None:
    now = datetime(2026, 9, 28, 18, tzinfo=UTC)
    started = now - timedelta(hours=2)
    result = calculate_consumption(started, now, "America/Santiago", [])
    assert result.status == "INSUFFICIENT_HISTORY"
    assert result.weekly_average is None
    unknown = assess_stock(D(7), None, result, now)
    assert unknown.safe_stock is None
    assert unknown.is_low_stock is None
    assert unknown.suggested_quantity is None
    assert unknown.depletion_status == "INSUFFICIENT_HISTORY"
    manual = assess_stock(D(7), D(10), result, now)
    assert manual.alert_status == "LOW_STOCK"
    assert manual.alert_source == "MANUAL_THRESHOLD"
    assert manual.suggested_quantity is None


def test_7_day_boundary_and_28_day_window() -> None:
    now = datetime(2026, 10, 1, 15, tzinfo=UTC)
    seven_days = calculate_consumption(
        datetime(2026, 9, 23, 15, tzinfo=UTC), now, "America/Santiago", []
    )
    assert seven_days.complete_days == 7
    assert seven_days.status == "ESTABLISHED"
    old = datetime(2026, 9, 1, 15, tzinfo=UTC)
    exits = [(old, D(100)), (datetime(2026, 9, 30, 15, tzinfo=UTC), D(4))]
    result = calculate_consumption(old, now, "America/Santiago", exits)
    assert result.complete_days == 28
    assert result.observed_quantity == D(4)


def test_first_day_and_current_day_excluded_including_dst() -> None:
    started = datetime(2026, 9, 10, 18, 30, tzinfo=UTC)  # 15:30 local
    now = datetime(2026, 9, 13, 21, tzinfo=UTC)
    exits = [
        (datetime(2026, 9, 10, 20, tzinfo=UTC), D(100)),
        (datetime(2026, 9, 11, 15, tzinfo=UTC), D(2)),
        (datetime(2026, 9, 13, 18, tzinfo=UTC), D(100)),
    ]
    result = calculate_consumption(started, now, "America/Santiago", exits)
    assert result.complete_days == 2
    assert result.observed_quantity == D(2)
    # Chile advances at local midnight: the civil day remains one complete day.
    dst_now = datetime(2026, 9, 7, 15, tzinfo=UTC)
    dst_started = datetime(2026, 9, 5, 3, tzinfo=UTC)
    dst = calculate_consumption(dst_started, dst_now, "America/Santiago", [])
    assert dst.complete_days == 2


def test_dst_day_uses_civil_boundaries_instead_of_24_hours() -> None:
    started = datetime(2026, 9, 5, 3, tzinfo=UTC)
    now = datetime(2026, 9, 7, 15, tzinfo=UTC)
    # 6 Sep begins at 01:00 local; the next midnight is only 23 hours later.
    exit_during_dst_day = datetime(2026, 9, 6, 4, tzinfo=UTC)
    result = calculate_consumption(started, now, "America/Santiago", [(exit_during_dst_day, D(4))])
    assert result.complete_days == 2
    assert result.observed_quantity == D(4)
    assert result.weekly_average == D(14)


def test_threshold_equality_override_and_nonnegative_need() -> None:
    now = datetime(2026, 9, 29, 12, tzinfo=UTC)
    # Use an exact summary to isolate P5 from the observed-day rate above.
    from app.domain.forecast import Consumption

    summary = Consumption("ESTABLISHED", 28, D(40), D(10), None, None)
    equal = assess_stock(D(10), None, summary, now)
    assert equal.alert_status == "LOW_STOCK"
    assert equal.safe_stock == D(10)
    assert equal.target_stock == D(20)
    assert equal.suggested_quantity == D(10)
    assert assess_stock(D(7), None, summary, now).suggested_quantity == D(13)
    override = assess_stock(D(11), D(12), summary, now)
    assert override.alert_status == "LOW_STOCK"
    assert override.safe_stock == D(12)
    assert assess_stock(D(30), None, summary, now).suggested_quantity == D(0)


def test_positive_consumption_with_unrepresentable_horizon() -> None:
    from app.domain.forecast import Consumption

    now = datetime(2026, 9, 29, 12, tzinfo=UTC)
    summary = Consumption("ESTABLISHED", 28, D("0.000001"), D("0.00000025"), None, None)
    assessment = assess_stock(D(1000), None, summary, now)
    assert assessment.daily_average > 0
    assert assessment.remaining_days == D(28_000_000_000)
    assert assessment.depletion_at is None
    assert assessment.depletion_status == "OUT_OF_RANGE"
