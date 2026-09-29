from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from app.domain.errors import RuleViolation
from app.domain.history import CountFact, MovementFact, validate_history


def fact(id: int, kind: str, delta: int, invalidates: int | None = None) -> MovementFact:
    at = datetime(2026, 9, id + 1, tzinfo=UTC)
    return MovementFact(id, kind, D(delta), at, at, invalidates)


def test_negative_history_is_rejected_even_if_later_entry_restores_stock() -> None:
    movements = [fact(1, "INITIAL", 10), fact(2, "EXIT", -12), fact(3, "ENTRY", 10)]
    with pytest.raises(RuleViolation, match="Historical stock would be negative"):
        validate_history(movements, [])


def test_count_checkpoint_blocks_nonnegative_backdated_event() -> None:
    movements = [fact(1, "INITIAL", 10), fact(2, "ENTRY", 1), fact(3, "COUNT_ADJUSTMENT", 0)]
    count = CountFact(3, D(10), D(10))
    with pytest.raises(RuleViolation) as error:
        validate_history(movements, [count])
    assert error.value.code == "HISTORICAL_RECONCILIATION_CONFLICT"


def test_zero_effect_retroactive_documentation_preserves_checkpoint() -> None:
    original = fact(1, "INITIAL", 10)
    count_movement = fact(3, "COUNT_ADJUSTMENT", 0)
    retro_created = MovementFact(
        4,
        "CORRECTION",
        D(0),
        datetime(2026, 9, 3, tzinfo=UTC),
        datetime(2026, 9, 5, tzinfo=UTC),
        None,
    )
    movements = [original, count_movement, retro_created]
    assert validate_history(movements, [CountFact(3, D(10), D(10))]) == D(10)


def test_reversing_count_does_not_erase_later_count() -> None:
    movements = [
        fact(1, "INITIAL", 10),
        fact(2, "COUNT_ADJUSTMENT", 5),
        fact(3, "COUNT_ADJUSTMENT", -1),
        fact(4, "REVERSAL", -5, 2),
    ]
    counts = [CountFact(2, D(10), D(15)), CountFact(3, D(15), D(14))]
    with pytest.raises(RuleViolation) as error:
        validate_history(movements, counts)
    assert error.value.code == "HISTORICAL_RECONCILIATION_CONFLICT"


def test_reversed_count_still_preserves_its_historical_pre_count_snapshot() -> None:
    initial = fact(1, "INITIAL", 10)
    count_adjustment = fact(3, "COUNT_ADJUSTMENT", 5)
    reversal = fact(5, "REVERSAL", -5, 3)
    confirmed = CountFact(3, D(10), D(15))
    assert validate_history([initial, count_adjustment, reversal], [confirmed]) == D(10)
    retro_created = MovementFact(
        6,
        "ENTRY",
        D(1),
        datetime(2026, 9, 3, tzinfo=UTC),
        datetime(2026, 9, 7, tzinfo=UTC),
        None,
    )
    with pytest.raises(RuleViolation) as error:
        validate_history([initial, count_adjustment, reversal, retro_created], [confirmed])
    assert error.value.code == "HISTORICAL_RECONCILIATION_CONFLICT"


def test_same_instant_order_does_not_depend_on_query_order() -> None:
    initial = fact(1, "INITIAL", 10)
    count_movement = fact(2, "COUNT_ADJUSTMENT", 0)
    later_created_entry = MovementFact(
        3,
        "ENTRY",
        D(1),
        count_movement.occurred_at,
        count_movement.created_at + timedelta(days=1),
        None,
    )
    count = CountFact(2, D(10), D(10))
    for facts in (
        [initial, count_movement, later_created_entry],
        [later_created_entry, count_movement, initial],
    ):
        assert validate_history(facts, [count]) == D(11)
