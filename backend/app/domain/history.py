"""Validate immutable movement history and confirmed count checkpoints (D1/D6)."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.domain.errors import RuleViolation


@dataclass(frozen=True)
class MovementFact:
    id: int
    kind: str
    delta: Decimal
    occurred_at: datetime
    created_at: datetime
    invalidates_movement_id: int | None


@dataclass(frozen=True)
class CountFact:
    adjustment_movement_id: int
    theoretical_before: Decimal
    physical_stock: Decimal


def effective_movements(movements: list[MovementFact]) -> list[MovementFact]:
    reversed_ids = {
        movement.invalidates_movement_id for movement in movements if movement.kind == "REVERSAL"
    }
    return sorted(
        (
            movement
            for movement in movements
            if movement.kind != "REVERSAL" and movement.id not in reversed_ids
        ),
        key=lambda movement: (movement.occurred_at, movement.created_at, movement.id),
    )


def validate_history(movements: list[MovementFact], counts: list[CountFact]) -> Decimal:
    count_by_adjustment = {count.adjustment_movement_id: count for count in counts}
    reversed_ids = {
        movement.invalidates_movement_id for movement in movements if movement.kind == "REVERSAL"
    }
    balance = Decimal(0)
    ordered = sorted(
        (m for m in movements if m.kind != "REVERSAL"),
        key=lambda m: (m.occurred_at, m.created_at, m.id),
    )
    for movement in ordered:
        count = count_by_adjustment.get(movement.id)
        if count is not None and balance != count.theoretical_before:
            raise RuleViolation(
                "HISTORICAL_RECONCILIATION_CONFLICT",
                "Operation would alter a confirmed physical count",
            )
        # A reversed count loses its stock/metric effect, not its immutable pre-count snapshot.
        if movement.id in reversed_ids:
            continue
        balance += movement.delta
        if balance < 0:
            raise RuleViolation("HISTORICAL_NEGATIVE_STOCK", "Historical stock would be negative")
        if count is not None and balance != count.physical_stock:
            raise RuleViolation(
                "HISTORICAL_RECONCILIATION_CONFLICT",
                "Operation would alter a confirmed physical count",
            )
    return balance
