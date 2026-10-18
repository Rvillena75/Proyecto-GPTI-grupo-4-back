"""Period indicators from persisted events. E3 causal comparisons are not inferred."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.domain.errors import RuleViolation
from app.domain.history import effective_movements
from app.persistence.models import Item, Movement, Order, Suggestion
from app.services.inventory import InventoryService


def metrics(session: Session, start: datetime, end: datetime) -> dict:
    if any(t.tzinfo is None or t.utcoffset() is None for t in (start, end)) or end <= start:
        raise RuleViolation("INVALID_PERIOD", "Use un período [inicio, fin) con zona", 422)
    with session.begin():
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        return _snapshot(session, start, end)


def _snapshot(session: Session, start: datetime, end: datetime) -> dict:
    approved = list(
        session.execute(
            select(Order, Suggestion)
            .join(Suggestion, Suggestion.id == Order.suggestion_id)
            .where(Order.decided_at >= start, Order.decided_at < end)
            .order_by(Order.id)
        )
    )
    before, evaluated, legacy = [], [], []
    for order, suggestion in approved:
        forecast = suggestion.inputs_snapshot.get("inventory", {}).get("assessment", {})
        depletion = forecast.get("depletion_at")
        if depletion is None:
            legacy.append(order.id)
            continue
        evaluated.append(order.id)
        # Attended = approval, per RN-06. Does not claim that delivery avoided a stockout.
        if order.decided_at < datetime.fromisoformat(depletion):
            before.append(order.id)
    per_item = []
    for item in session.scalars(select(Item).order_by(Item.id)):
        if item.created_at >= end:
            continue
        ledger = list(session.scalars(select(Movement).where(Movement.item_id == item.id)))
        events, _ = InventoryService(session)._facts_for_metrics(ledger)
        running, stockouts = Decimal(0), []
        for event in effective_movements(events):
            after = running + event.delta
            if running > 0 and after == 0 and start <= event.occurred_at < end:
                stockouts.append(event.id)
            running = after
        rate = InventoryService(session).shortage_rate(item.id, start, end, _lock=False)
        per_item.append(
            {
                "item_id": item.id,
                "base_unit": item.base_unit,
                "shortage": rate,
                "stockout_count": len(stockouts),
                "stockout_movement_ids": stockouts,
            }
        )
    costs = sum((s.total_clp for _, s in approved if s.total_clp is not None), Decimal(0))
    savings_rows = [
        (o, s)
        for o, s in approved
        if s.savings_clp is not None and s.next_best_total_clp is not None
    ]
    savings = sum((s.savings_clp for _, s in savings_rows), Decimal(0))
    alternative = sum((s.next_best_total_clp for _, s in savings_rows), Decimal(0))
    return {
        "start": start,
        "end": end,
        "interval": "[start,end)",
        "approved_orders": len(approved),
        "order_ids": [o.id for o, _ in approved],
        "approved_total_clp": str(costs)
        if all(s.total_clp is not None for _, s in approved)
        else None,
        "estimated_savings_clp": str(savings)
        if savings_rows and len(savings_rows) == len(approved)
        else None,
        "estimated_savings_percent": (
            str(savings * 100 / alternative)
            if alternative > 0 and len(savings_rows) == len(approved)
            else None
        ),
        "savings_order_ids": [o.id for o, _ in savings_rows],
        "alerts_attended_before_depletion": len(before) if evaluated and not legacy else None,
        "attended_order_ids": before,
        "evaluated_alert_order_ids": evaluated,
        "not_evaluable_order_ids": legacy,
        "items": per_item,
        "stockouts": sum(i["stockout_count"] for i in per_item),
        "definitions": {
            "attended": (
                "Approved suggestions with frozen depletion_at later than approval; "
                "no claim of avoided stockouts."
            ),
            "stockouts": (
                "Positive-to-zero transitions in the effective inventory ledger; "
                "lost sales/unrecorded demand are not observed."
            ),
            "shortage": "RN-16 by item/base unit. Incompatible units are never summed.",
            "savings": (
                "RN-11 frozen next-best feasible pack cost at suggestion time; "
                "approved orders only."
            ),
        },
        "impact_comparison": {
            "status": "NOT_VERIFIABLE",
            "stockout_reduction_percent": None,
            "shortage_reduction_percent": None,
            "reason": "No comparable E3 baseline or business experiment provided",
        },
    }
