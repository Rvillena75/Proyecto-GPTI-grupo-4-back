"""P3 count and shortage calculations, independent of the database."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class CountCalculation:
    difference: Decimal
    shortage: Decimal


@dataclass(frozen=True)
class Percentage:
    status: str
    value: Decimal | None


def calculate_count(theoretical: Decimal, physical: Decimal) -> CountCalculation:
    difference = physical - theoretical
    return CountCalculation(difference, max(theoretical - physical, Decimal(0)))


def shortage_rate(
    stock_at_start: Decimal | None,
    entries: Decimal,
    positive_effective_adjustments: Decimal,
    shortages: Decimal,
) -> Percentage:
    if stock_at_start is None:
        return Percentage("NOT_CALCULABLE", None)
    available = stock_at_start + entries + positive_effective_adjustments
    if available <= 0:
        return Percentage("NOT_CALCULABLE", None)
    return Percentage("CALCULABLE", shortages / available * Decimal(100))


def shortage_reduction(baseline: Decimal | None, pilot: Decimal | None) -> Percentage:
    if baseline is None or baseline <= 0 or pilot is None:
        return Percentage("NOT_VERIFIABLE", None)
    return Percentage("CALCULABLE", (baseline - pilot) / baseline * Decimal(100))
