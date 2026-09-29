"""P4/P5 deterministic calendar-day consumption and stock status."""

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


def assess_stock(
    current_stock: Decimal,
    manual_safe_stock: Decimal | None,
    consumption: Consumption,
    reference_at: datetime,
) -> StockAssessment:
    weekly = consumption.weekly_average
    safe = manual_safe_stock if manual_safe_stock is not None else weekly
    source = (
        "MANUAL_THRESHOLD"
        if manual_safe_stock is not None
        else ("AUTOMATIC" if weekly is not None else None)
    )
    if safe is None:
        alert_status, is_low = "INSUFFICIENT_HISTORY", None
    else:
        is_low = current_stock <= safe
        alert_status = "LOW_STOCK" if is_low else "OK"
    target = weekly * 2 if weekly is not None else None
    suggested = max(Decimal(0), target - current_stock) if target is not None else None
    daily = weekly / 7 if weekly is not None else None
    remaining = current_stock / daily if daily is not None and daily > 0 else None
    depletion = None
    if weekly is None:
        depletion_status = "INSUFFICIENT_HISTORY"
    elif weekly == 0:
        depletion_status = "ZERO_CONSUMPTION"
    else:
        depletion_status = "OUT_OF_RANGE"
        microseconds = (remaining * Decimal(86_400_000_000)).to_integral_value(
            rounding=ROUND_HALF_UP
        )
        try:
            depletion = reference_at + timedelta(microseconds=int(microseconds))
        except OverflowError:
            pass
        else:
            depletion_status = "PROJECTED"
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
    )
