"""P4/P5 deterministic calendar-day consumption and stock status (alert per C1/C5/C12)."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Consumption:
    status: str
    complete_days: int
    observed_quantity: Decimal
    weekly_average: Decimal | None
    window_start: datetime | None
    window_end: datetime | None


@dataclass(frozen=True)
class StockAssessment:
    safe_stock: Decimal | None
    alert_source: str | None
    alert_status: str
    is_low_stock: bool | None
    target_stock: Decimal | None
    suggested_quantity: Decimal | None
    daily_average: Decimal | None
    remaining_days: Decimal | None
    depletion_at: datetime | None
    depletion_status: str
    lead_time_days: int | None = None
    projected_stock_at_arrival: Decimal | None = None
    in_transit_quantity: Decimal = Decimal(0)
    order_by_at: datetime | None = None


def local_day_start(day: date, zone: ZoneInfo) -> datetime:
    """Return the first real instant of a local civil day, including DST midnight gaps."""
    local_midnight = datetime.combine(day, time.min, zone)
    return local_midnight.astimezone(UTC).astimezone(zone)


def calculate_consumption(
    observation_started_at: datetime,
    reference_at: datetime,
    timezone_name: str,
    exits: list[tuple[datetime, Decimal]],
) -> Consumption:
    if observation_started_at.tzinfo is None or reference_at.tzinfo is None:
        raise ValueError("Timestamps must be timezone-aware")
    zone = ZoneInfo(timezone_name)
    local_reference = reference_at.astimezone(zone)
    local_start = observation_started_at.astimezone(zone)
    first_day = local_start.date()
    if observation_started_at > local_day_start(first_day, zone):
        first_day += timedelta(days=1)
    today = local_reference.date()
    first_day = max(first_day, today - timedelta(days=28))
    complete_days = max(0, (today - first_day).days)
    if complete_days == 0:
        return Consumption("INSUFFICIENT_HISTORY", 0, Decimal(0), None, None, None)
    start = local_day_start(first_day, zone)
    end = local_day_start(today, zone)
    observed = sum((quantity for at, quantity in exits if start <= at < end), Decimal(0))
    weekly = observed / Decimal(complete_days) * Decimal(7)
    status = "PRELIMINARY" if complete_days < 7 else "ESTABLISHED"
    return Consumption(status, complete_days, observed, weekly, start, end)


def _after_days(reference_at: datetime, days: Decimal) -> datetime | None:
    microseconds = (days * Decimal(86_400_000_000)).to_integral_value(rounding=ROUND_HALF_UP)
    try:
        return reference_at + timedelta(microseconds=int(microseconds))
    except OverflowError:
        return None


def assess_stock(
    current_stock: Decimal,
    manual_safe_stock: Decimal | None,
    consumption: Consumption,
    reference_at: datetime,
    lead_time_days: int | None = None,
    in_transit: Decimal = Decimal(0),
) -> StockAssessment:
    """Stock status with the agreed replenishment rules.

    - C12: ``lead_time_days`` is the longest delivery time, in calendar days, among the item's
      active suppliers; ``None`` (no supplier yet) behaves as zero days.
    - C1: alert when the stock projected at the arrival of an order placed now is at or below
      the safe stock (``<=``, not ``<``).
    - An approved order not yet received (``in_transit``) marks a low-stock alert as attended
      (``IN_TRANSIT``) and is discounted from the suggested quantity.
    - C5: suggested = max(0, 2 weeks of consumption - projected stock at arrival - in transit).
    """
    weekly = consumption.weekly_average
    safe = manual_safe_stock if manual_safe_stock is not None else weekly
    source = (
        "MANUAL_THRESHOLD"
        if manual_safe_stock is not None
        else ("AUTOMATIC" if weekly is not None else None)
    )
    daily = weekly / 7 if weekly is not None else None
    lead = Decimal(lead_time_days or 0)
    projected = max(Decimal(0), current_stock - daily * lead) if daily is not None else None
    compared = projected if projected is not None else current_stock
    if safe is None:
        alert_status, is_low = "INSUFFICIENT_HISTORY", None
    else:
        is_low = compared <= safe
        if not is_low:
            alert_status = "OK"
        else:
            alert_status = "IN_TRANSIT" if in_transit > 0 else "LOW_STOCK"
    target = weekly * 2 if weekly is not None else None
    suggested = (
        max(Decimal(0), target - projected - in_transit)
        if target is not None and projected is not None
        else None
    )
    order_by = None
    if safe is not None and daily is not None and daily > 0:
        order_by = _after_days(reference_at, (current_stock - safe) / daily - lead)
    remaining = current_stock / daily if daily is not None and daily > 0 else None
    depletion = None
    if weekly is None:
        depletion_status = "INSUFFICIENT_HISTORY"
    elif weekly == 0:
        depletion_status = "ZERO_CONSUMPTION"
    else:
        depletion = _after_days(reference_at, remaining)
        depletion_status = "PROJECTED" if depletion is not None else "OUT_OF_RANGE"
    return StockAssessment(
        safe,
        source,
        alert_status,
        is_low,
        target,
        suggested,
        daily,
        remaining,
        depletion,
        depletion_status,
        lead_time_days,
        projected,
        in_transit,
        order_by,
    )
