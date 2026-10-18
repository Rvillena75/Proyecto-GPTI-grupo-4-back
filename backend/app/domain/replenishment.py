"""C12 supplier selection; suppliers and order persistence remain Pedro's contracts."""

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from app.domain.errors import RuleViolation
from app.domain.forecast import Consumption, StockAssessment, assess_stock
from app.domain.units import amount


@dataclass(frozen=True)
class SupplierLeadTime:
    supplier_id: int
    lead_time_days: int
    is_active: bool = True

    def __post_init__(self) -> None:
        if type(self.lead_time_days) is not int or self.lead_time_days < 0:
            raise RuleViolation("INVALID_LEAD_TIME", "Use nonnegative calendar days", 422)


def maximum_active_lead_time(suppliers: list[SupplierLeadTime]) -> int:
    active = [supplier.lead_time_days for supplier in suppliers if supplier.is_active]
    if not active:
        raise RuleViolation("NO_ACTIVE_SUPPLIERS", "Configure an active supplier for this item")
    return max(active)


@dataclass(frozen=True)
class ReplenishmentAssessment(StockAssessment):
    lead_time_days: int = 0
    projected_arrival_stock: Decimal | None = None
    in_transit_quantity: Decimal = Decimal(0)


def assess_replenishment(
    current_stock: Decimal,
    manual_safe_stock: Decimal | None,
    consumption: Consumption,
    reference_at: datetime,
    *,
    lead_time_days: int = 0,
    in_transit_quantity: Decimal = Decimal(0),
) -> ReplenishmentAssessment:
    """C1/C5/C12 compose Pedro's unchanged consumption/safe-stock/forecast rules."""
    if type(lead_time_days) is not int or lead_time_days < 0:
        raise RuleViolation("INVALID_LEAD_TIME", "Use nonnegative calendar days", 422)
    amount(current_stock, allow_zero=True)
    transit = amount(in_transit_quantity, allow_zero=True)
    base = assess_stock(
        current_stock, manual_safe_stock, consumption, reference_at, lead_time_days, transit
    )
    return ReplenishmentAssessment(
        **{
            **asdict(base),
            "lead_time_days": lead_time_days,
            "projected_arrival_stock": base.projected_stock_at_arrival,
            "in_transit_quantity": transit,
        },
    )
