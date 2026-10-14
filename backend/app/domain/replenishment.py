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
    required_quantity: Decimal | None = None


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
    base = assess_stock(current_stock, manual_safe_stock, consumption, reference_at)
    projected = (
        current_stock - base.daily_average * lead_time_days
        if base.daily_average is not None
        else (current_stock if lead_time_days == 0 else None)
    )
    if base.safe_stock is None or projected is None:
        alert_status, is_low = "INSUFFICIENT_HISTORY", None
    else:
        is_low = projected <= base.safe_stock
        alert_status = "LOW_STOCK" if is_low else "OK"
    required = (
        max(Decimal(0), base.target_stock - projected - transit)
        if base.target_stock is not None and projected is not None
        else None
    )
    suggested = required
    if transit > 0:
        alert_status, suggested = "IN_TRANSIT", Decimal(0)
    return ReplenishmentAssessment(
        **{
            **asdict(base),
            "alert_status": alert_status,
            "is_low_stock": is_low,
            "suggested_quantity": suggested,
            "lead_time_days": lead_time_days,
            "projected_arrival_stock": projected,
            "in_transit_quantity": transit,
            "required_quantity": required,
        },
    )
