from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from threading import Barrier, Event

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.domain.errors import RuleViolation
from app.persistence.models import Count, Item, Observation
from app.services.inventory import InventoryService

T0 = datetime(2026, 9, 1, 12, tzinfo=UTC)


def call(factory: sessionmaker[Session], method: str, *args, **kwargs):
    with factory() as session:
        return getattr(InventoryService(session), method)(*args, **kwargs)


def create(factory: sessionmaker[Session], stock: str = "10", now: datetime = T0) -> Item:
    return call(
        factory, "create_item", "Azúcar", "kg", D(stock), "kg", "America/Santiago", None, now
    )


def test_migration_and_multiple_items_do_not_mix_balances(clean_db: sessionmaker[Session]) -> None:
    with clean_db() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0003_procurement"
    first = create(clean_db, "10")
    second = call(
        clean_db, "create_item", "Harina", "kg", D(20), "kg", "America/Santiago", None, T0
    )
    call(clean_db, "add_movement", first.id, "EXIT", D(3), "kg", T0 + timedelta(hours=1))
    assert call(clean_db, "status", first.id, T0 + timedelta(hours=2))["current_stock"] == D(7)
    assert call(clean_db, "status", second.id, T0 + timedelta(hours=2))["current_stock"] == D(20)


def test_units_exact_exit_and_failed_operations_are_atomic(clean_db: sessionmaker[Session]) -> None:
    item = create(clean_db, "1")
    entry = call(clean_db, "add_movement", item.id, "ENTRY", D(500), "g", T0 + timedelta(hours=1))
    assert entry.delta == D("0.5")
    exit_ = call(clean_db, "add_movement", item.id, "EXIT", D("1.5"), "kg", T0 + timedelta(hours=2))
    assert exit_.delta == D("-1.5")
    assert call(clean_db, "status", item.id, T0 + timedelta(hours=3))["current_stock"] == 0

    liquid = call(clean_db, "create_item", "Leche", "L", D(1), "L", "America/Santiago", None, T0)
    milliliters = call(
        clean_db, "add_movement", liquid.id, "ENTRY", D(500), "mL", T0 + timedelta(hours=1)
    )
    assert milliliters.delta == D("0.5")
    assert call(clean_db, "status", liquid.id, T0 + timedelta(hours=2))["current_stock"] == D("1.5")
    before = len(call(clean_db, "movements", item.id))
    with pytest.raises(RuleViolation) as error:
        call(clean_db, "add_movement", item.id, "EXIT", D(1), "kg", T0 + timedelta(hours=3))
    assert error.value.code == "NEGATIVE_STOCK"
    assert len(call(clean_db, "movements", item.id)) == before
    assert call(clean_db, "status", item.id, T0 + timedelta(hours=3))["current_stock"] == 0


def test_reversal_initial_replacement_and_immutable_observation(
    clean_db: sessionmaker[Session],
) -> None:
    item = create(clean_db)
    movement = call(clean_db, "add_movement", item.id, "EXIT", D(2), "kg", T0 + timedelta(hours=1))
    initial = call(clean_db, "movements", item.id)[0]
    with pytest.raises(RuleViolation) as error:
        call(clean_db, "reverse", initial.id, T0 + timedelta(hours=2))
    assert error.value.code == "INITIAL_STOCK_HAS_DEPENDENT_MOVEMENTS"
    reversal = call(clean_db, "reverse", movement.id, T0 + timedelta(hours=2))
    assert reversal.invalidates_movement_id == movement.id
    call(clean_db, "reverse", initial.id, T0 + timedelta(hours=3))
    assert (
        call(clean_db, "status", item.id, T0 + timedelta(hours=3))["observation_started_at"] is None
    )
    replacement = call(clean_db, "replace_initial", item.id, D(12), "kg", T0 + timedelta(hours=4))
    assert replacement.kind == "INITIAL"
    assert call(clean_db, "status", item.id, T0 + timedelta(hours=5))[
        "observation_started_at"
    ] == T0 + timedelta(hours=4)
    with clean_db() as session:
        starts = session.scalars(
            select(Observation.started_at)
            .where(Observation.item_id == item.id)
            .order_by(Observation.id)
        ).all()
    assert starts == [T0, T0 + timedelta(hours=4)]


def test_initial_error_with_dependent_history_is_corrected_by_linked_count(
    clean_db: sessionmaker[Session],
) -> None:
    item = create(clean_db, "10")
    initial = call(clean_db, "movements", item.id)[0]
    call(clean_db, "add_movement", item.id, "ENTRY", D(5), "kg", T0 + timedelta(hours=1))
    corrected = call(
        clean_db,
        "record_count",
        item.id,
        D(13),
        "kg",
        T0 + timedelta(hours=2),
        "error de registro",
        initial.id,
    )
    assert corrected.corrects_movement_id == initial.id
    assert corrected.difference == D(-2)
    assert corrected.shortage == D(2)  # P3 includes registration errors in observed shortage.
    assert call(clean_db, "status", item.id, T0 + timedelta(hours=3))["current_stock"] == D(13)


def test_reversal_and_backdating_reject_historical_negative_stock(
    clean_db: sessionmaker[Session],
) -> None:
    item = create(clean_db)
    entry = call(clean_db, "add_movement", item.id, "ENTRY", D(5), "kg", T0 + timedelta(days=1))
    call(clean_db, "add_movement", item.id, "EXIT", D(12), "kg", T0 + timedelta(days=2))
    call(clean_db, "add_movement", item.id, "ENTRY", D(10), "kg", T0 + timedelta(days=3))
    with pytest.raises(RuleViolation) as error:
        call(clean_db, "reverse", entry.id, T0 + timedelta(days=4))
    assert error.value.code == "HISTORICAL_NEGATIVE_STOCK"
    with pytest.raises(RuleViolation) as error:
        call(
            clean_db,
            "add_movement",
            item.id,
            "EXIT",
            D(5),
            "kg",
            T0 + timedelta(days=4),
            T0 + timedelta(hours=1),
        )
    assert error.value.code == "HISTORICAL_NEGATIVE_STOCK"
    assert call(clean_db, "status", item.id, T0 + timedelta(days=4))["current_stock"] == D(13)


def test_reversal_cannot_make_current_stock_negative(clean_db: sessionmaker[Session]) -> None:
    item = create(clean_db, "0")
    entry = call(clean_db, "add_movement", item.id, "ENTRY", D(10), "kg", T0 + timedelta(hours=1))
    call(clean_db, "add_movement", item.id, "EXIT", D(8), "kg", T0 + timedelta(hours=2))
    before = len(call(clean_db, "movements", item.id))
    with pytest.raises(RuleViolation) as error:
        call(clean_db, "reverse", entry.id, T0 + timedelta(hours=3))
    assert error.value.code == "NEGATIVE_STOCK"
    assert call(clean_db, "status", item.id, T0 + timedelta(hours=3))["current_stock"] == D(2)
    assert len(call(clean_db, "movements", item.id)) == before


def test_backdating_after_latest_count_is_allowed(clean_db: sessionmaker[Session]) -> None:
    item = create(clean_db, "10")
    count = call(clean_db, "record_count", item.id, D(10), "kg", T0 + timedelta(days=1))
    movement = call(
        clean_db,
        "add_movement",
        item.id,
        "ENTRY",
        D(2),
        "kg",
        T0 + timedelta(days=3),
        T0 + timedelta(days=2),
    )
    assert movement.created_at > movement.occurred_at
    assert call(clean_db, "status", item.id, T0 + timedelta(days=3))["current_stock"] == D(12)
    assert call(clean_db, "counts", item.id)[0].theoretical_before == count.theoretical_before


def test_movement_before_initial_observation_is_rejected(clean_db: sessionmaker[Session]) -> None:
    item = create(clean_db)
    with pytest.raises(RuleViolation) as error:
        call(
            clean_db,
            "add_movement",
            item.id,
            "ENTRY",
            D(1),
            "kg",
            T0 + timedelta(hours=1),
            T0 - timedelta(minutes=1),
        )
    assert error.value.code == "BEFORE_INITIAL_STOCK"
    assert call(clean_db, "status", item.id, T0 + timedelta(hours=1))["current_stock"] == D(10)


def test_confirmed_count_blocks_retroactive_entry_and_reversal(
    clean_db: sessionmaker[Session],
) -> None:
    item = create(clean_db)
    entry = call(clean_db, "add_movement", item.id, "ENTRY", D(5), "kg", T0 + timedelta(days=1))
    count = call(clean_db, "record_count", item.id, D(14), "kg", T0 + timedelta(days=2))
    before = len(call(clean_db, "movements", item.id))
    with pytest.raises(RuleViolation) as error:
        call(
            clean_db,
            "add_movement",
            item.id,
            "ENTRY",
            D(1),
            "kg",
            T0 + timedelta(days=3),
            T0 + timedelta(hours=1),
        )
    assert error.value.code == "HISTORICAL_RECONCILIATION_CONFLICT"
    with pytest.raises(RuleViolation) as error:
        call(clean_db, "reverse", entry.id, T0 + timedelta(days=3), T0 + timedelta(days=1, hours=1))
    assert error.value.code == "HISTORICAL_RECONCILIATION_CONFLICT"
    assert len(call(clean_db, "movements", item.id)) == before
    assert call(clean_db, "counts", item.id)[0].theoretical_before == count.theoretical_before


def test_current_documentary_correction_preserves_count_and_stock(
    clean_db: sessionmaker[Session],
) -> None:
    item = create(clean_db)
    wrong = call(clean_db, "add_movement", item.id, "EXIT", D(5), "kg", T0 + timedelta(days=1))
    count = call(clean_db, "record_count", item.id, D(10), "kg", T0 + timedelta(days=2))
    original = (
        count.theoretical_before,
        count.physical_stock,
        count.difference,
        count.shortage,
        count.adjustment_movement_id,
    )
    correction = call(
        clean_db, "record_correction", wrong.id, "Duplicate exit", T0 + timedelta(days=3)
    )
    assert correction.delta == 0
    assert correction.invalidates_movement_id == wrong.id
    assert call(clean_db, "status", item.id, T0 + timedelta(days=3))["current_stock"] == D(10)
    after = call(clean_db, "counts", item.id)[0]
    assert (
        after.theoretical_before,
        after.physical_stock,
        after.difference,
        after.shortage,
        after.adjustment_movement_id,
    ) == original
    corrected_state = call(clean_db, "status", item.id, T0 + timedelta(days=4))
    assert corrected_state["consumption"].weekly_average == 0
    rate = call(clean_db, "shortage_rate", item.id, T0, T0 + timedelta(days=4))
    assert rate["status"] == "NOT_CALCULABLE"
    assert rate["rate_percent"] is None
    with pytest.raises(RuleViolation):
        call(clean_db, "record_correction", wrong.id, "Again", T0 + timedelta(days=4))


def test_same_timestamp_has_deterministic_count_order(clean_db: sessionmaker[Session]) -> None:
    item = create(clean_db)
    wrong = call(clean_db, "add_movement", item.id, "EXIT", D(2), "kg", T0 + timedelta(days=1))
    count = call(clean_db, "record_count", item.id, D(10), "kg", T0 + timedelta(days=1))
    correction = call(
        clean_db, "record_correction", wrong.id, "Count absorbed it", T0 + timedelta(days=2)
    )
    assert correction.delta == 0
    assert call(clean_db, "counts", item.id)[0].id == count.id


def test_new_movement_at_count_instant_is_after_it_but_before_later_counts(
    clean_db: sessionmaker[Session],
) -> None:
    item = create(clean_db)
    first_at = T0 + timedelta(days=1)
    first = call(clean_db, "record_count", item.id, D(10), "kg", first_at)
    entry = call(
        clean_db,
        "add_movement",
        item.id,
        "ENTRY",
        D(1),
        "kg",
        T0 + timedelta(days=3),
        first_at,
    )
    assert entry.occurred_at == first.occurred_at
    assert call(clean_db, "status", item.id, T0 + timedelta(days=3))["current_stock"] == D(11)
    assert call(clean_db, "counts", item.id)[0].theoretical_before == D(10)
    second = call(clean_db, "record_count", item.id, D(11), "kg", T0 + timedelta(days=4))
    before = len(call(clean_db, "movements", item.id))
    with pytest.raises(RuleViolation) as error:
        call(
            clean_db,
            "add_movement",
            item.id,
            "ENTRY",
            D(1),
            "kg",
            T0 + timedelta(days=5),
            first_at,
        )
    assert error.value.code == "HISTORICAL_RECONCILIATION_CONFLICT"
    assert len(call(clean_db, "movements", item.id)) == before
    assert [
        (c.theoretical_before, c.physical_stock) for c in call(clean_db, "counts", item.id)
    ] == [
        (first.theoretical_before, first.physical_stock),
        (second.theoretical_before, second.physical_stock),
    ]


def test_present_correction_with_stock_effect_keeps_old_count(
    clean_db: sessionmaker[Session],
) -> None:
    item = create(clean_db)
    wrong = call(clean_db, "add_movement", item.id, "EXIT", D(5), "kg", T0 + timedelta(days=1))
    count = call(clean_db, "record_count", item.id, D(10), "kg", T0 + timedelta(days=2))
    correction = call(
        clean_db,
        "record_correction",
        wrong.id,
        "Further present adjustment",
        T0 + timedelta(days=3),
        D(2),
    )
    assert correction.kind == "CORRECTION"
    assert correction.delta == D(2)
    assert correction.occurred_at == correction.created_at == T0 + timedelta(days=3)
    assert correction.invalidates_movement_id == wrong.id
    state = call(clean_db, "status", item.id, T0 + timedelta(days=4))
    assert state["current_stock"] == D(12)
    assert state["consumption"].weekly_average == 0  # correction is not consumption
    persisted = call(clean_db, "counts", item.id)[0]
    assert (
        persisted.theoretical_before,
        persisted.physical_stock,
        persisted.difference,
        persisted.shortage,
        persisted.adjustment_movement_id,
    ) == (
        count.theoretical_before,
        count.physical_stock,
        count.difference,
        count.shortage,
        count.adjustment_movement_id,
    )
    rate = call(clean_db, "shortage_rate", item.id, T0, T0 + timedelta(days=4))
    assert rate["status"] == "NOT_CALCULABLE"
    assert rate["rate_percent"] is None


def test_present_correction_cannot_make_stock_negative(clean_db: sessionmaker[Session]) -> None:
    item = create(clean_db)
    wrong = call(clean_db, "add_movement", item.id, "ENTRY", D(5), "kg", T0 + timedelta(days=1))
    call(clean_db, "record_count", item.id, D(15), "kg", T0 + timedelta(days=2))
    before = len(call(clean_db, "movements", item.id))
    with pytest.raises(RuleViolation) as error:
        call(
            clean_db,
            "record_correction",
            wrong.id,
            "Would be negative",
            T0 + timedelta(days=3),
            D(-16),
        )
    assert error.value.code == "NEGATIVE_STOCK"
    assert len(call(clean_db, "movements", item.id)) == before
    assert call(clean_db, "status", item.id, T0 + timedelta(days=4))["current_stock"] == D(15)


def test_count_formulas_rate_and_reversals_do_not_double_count(
    clean_db: sessionmaker[Session],
) -> None:
    item = create(clean_db, "20")
    wrong = call(clean_db, "add_movement", item.id, "ENTRY", D(10), "kg", T0 + timedelta(hours=1))
    call(clean_db, "reverse", wrong.id, T0 + timedelta(hours=2))
    first = call(clean_db, "record_count", item.id, D(18), "kg", T0 + timedelta(hours=3))
    assert first.shortage == D(2)
    rate = call(clean_db, "shortage_rate", item.id, T0, T0 + timedelta(days=1))
    assert rate["available"] == D(20)
    assert rate["rate_percent"] == D(10)

    second = create(clean_db, "20", T0 + timedelta(days=2))
    call(clean_db, "add_movement", second.id, "ENTRY", D(10), "kg", T0 + timedelta(days=2, hours=1))
    surplus = call(
        clean_db, "record_count", second.id, D(32), "kg", T0 + timedelta(days=2, hours=2)
    )
    shortage = call(
        clean_db, "record_count", second.id, D(29), "kg", T0 + timedelta(days=2, hours=3)
    )
    assert surplus.difference == D(2) and surplus.shortage == 0
    assert shortage.difference == D(-3) and shortage.shortage == D(3)
    rate = call(
        clean_db,
        "shortage_rate",
        second.id,
        T0 + timedelta(days=2),
        T0 + timedelta(days=3),
    )
    assert rate["available"] == D(32)
    assert rate["rate_percent"] == D("9.375")


def test_exact_p3_count_examples(clean_db: sessionmaker[Session]) -> None:
    item = create(clean_db, "25")
    shortage = call(clean_db, "record_count", item.id, D(22), "kg", T0 + timedelta(hours=1))
    assert (shortage.theoretical_before, shortage.difference, shortage.shortage) == (
        D(25),
        D(-3),
        D(3),
    )
    assert call(clean_db, "status", item.id, T0 + timedelta(hours=2))["current_stock"] == D(22)
    surplus = call(clean_db, "record_count", item.id, D(24), "kg", T0 + timedelta(hours=3))
    assert (surplus.theoretical_before, surplus.difference, surplus.shortage) == (
        D(22),
        D(2),
        D(0),
    )
    assert call(clean_db, "status", item.id, T0 + timedelta(hours=4))["current_stock"] == D(24)


def test_reversed_count_keeps_audit_record_but_removes_shortage(
    clean_db: sessionmaker[Session],
) -> None:
    item = create(clean_db, "20")
    count = call(clean_db, "record_count", item.id, D(15), "kg", T0 + timedelta(hours=1))
    assert count.shortage == D(5)
    call(clean_db, "reverse", count.adjustment_movement_id, T0 + timedelta(hours=2))
    assert call(clean_db, "status", item.id, T0 + timedelta(hours=3))["current_stock"] == D(20)
    assert call(clean_db, "counts", item.id)[0].shortage == D(5)
    rate = call(clean_db, "shortage_rate", item.id, T0, T0 + timedelta(days=1))
    assert rate["shortage"] == 0
    assert rate["rate_percent"] == 0


def test_correction_linked_to_new_count_changes_current_stock_only(
    clean_db: sessionmaker[Session],
) -> None:
    item = create(clean_db, "10")
    wrong = call(clean_db, "add_movement", item.id, "EXIT", D(5), "kg", T0 + timedelta(days=1))
    first = call(clean_db, "record_count", item.id, D(10), "kg", T0 + timedelta(days=2))
    correction_count = call(
        clean_db,
        "record_count",
        item.id,
        D(8),
        "kg",
        T0 + timedelta(days=3),
        "error de registro",
        wrong.id,
    )
    assert correction_count.theoretical_before == D(10)
    assert correction_count.shortage == D(2)
    assert correction_count.corrects_movement_id == wrong.id
    assert call(clean_db, "status", item.id, T0 + timedelta(days=3))["current_stock"] == D(8)
    assert call(clean_db, "counts", item.id)[0].id == first.id
    assert call(clean_db, "counts", item.id)[0].physical_stock == D(10)


def test_zero_denominator_and_bad_period(clean_db: sessionmaker[Session]) -> None:
    item = create(clean_db, "0")
    result = call(clean_db, "shortage_rate", item.id, T0, T0 + timedelta(days=1))
    assert result["status"] == "NOT_CALCULABLE"
    assert result["rate_percent"] is None
    before_observation = call(
        clean_db, "shortage_rate", item.id, T0 - timedelta(days=1), T0 + timedelta(days=1)
    )
    assert before_observation["status"] == "NOT_CALCULABLE"
    with pytest.raises(RuleViolation):
        call(clean_db, "shortage_rate", item.id, T0, T0)


def test_p4_ignores_entries_counts_and_reversed_exits(clean_db: sessionmaker[Session]) -> None:
    item = create(clean_db, "20")
    exit_ = call(clean_db, "add_movement", item.id, "EXIT", D(4), "kg", T0 + timedelta(days=1))
    call(clean_db, "reverse", exit_.id, T0 + timedelta(days=1, hours=1))
    call(clean_db, "add_movement", item.id, "ENTRY", D(5), "kg", T0 + timedelta(days=2))
    call(clean_db, "record_count", item.id, D(26), "kg", T0 + timedelta(days=3))
    state = call(clean_db, "status", item.id, T0 + timedelta(days=9))
    assert state["consumption"].status == "ESTABLISHED"
    assert state["consumption"].weekly_average == 0
    assert state["assessment"].depletion_at is None


def test_count_and_adjustment_rollback_together(clean_db: sessionmaker[Session]) -> None:
    item = create(clean_db)

    def fail_after_insert(mapper, connection, target):
        raise RuntimeError("injected count failure")

    event.listen(Count, "after_insert", fail_after_insert)
    try:
        with pytest.raises(RuntimeError, match="injected count failure"):
            call(clean_db, "record_count", item.id, D(8), "kg", T0 + timedelta(hours=1))
    finally:
        event.remove(Count, "after_insert", fail_after_insert)
    assert call(clean_db, "status", item.id, T0 + timedelta(hours=2))["current_stock"] == D(10)
    assert call(clean_db, "counts", item.id) == []
    assert len(call(clean_db, "movements", item.id)) == 1


def test_database_rejects_history_updates_and_deletes(clean_db: sessionmaker[Session]) -> None:
    item = create(clean_db)
    count = call(clean_db, "record_count", item.id, D(9), "kg", T0 + timedelta(hours=1))
    movement = call(clean_db, "movements", item.id)[0]
    statements = [
        text("UPDATE movements SET delta = 999 WHERE id = :id"),
        text("DELETE FROM movements WHERE id = :id"),
        text("UPDATE counts SET shortage = 999 WHERE id = :id"),
        text("DELETE FROM counts WHERE id = :id"),
    ]
    for statement, target in zip(
        statements, [movement.id, movement.id, count.id, count.id], strict=True
    ):
        with pytest.raises(DBAPIError), clean_db.begin() as session:
            session.execute(statement, {"id": target})
    with pytest.raises(DBAPIError), clean_db.begin() as session:
        session.execute(
            text(
                "UPDATE observations SET started_at = started_at + interval '1 day' "
                "WHERE item_id = :id"
            ),
            {"id": item.id},
        )


def test_database_rejects_direct_balance_divergence(clean_db: sessionmaker[Session]) -> None:
    item = create(clean_db)
    with pytest.raises(DBAPIError), clean_db.begin() as session:
        session.execute(
            text("UPDATE items SET current_stock = 777 WHERE id = :id"), {"id": item.id}
        )
    assert call(clean_db, "status", item.id, T0 + timedelta(hours=1))["current_stock"] == D(10)
    with clean_db() as session:
        assert session.scalar(
            text("SELECT SUM(delta) FROM movements WHERE item_id = :id"), {"id": item.id}
        ) == D(10)


def test_concurrent_exits_cannot_overspend_stock(clean_db: sessionmaker[Session]) -> None:
    item = create(clean_db)
    barrier = Barrier(2)

    def withdraw() -> str:
        barrier.wait()
        try:
            call(clean_db, "add_movement", item.id, "EXIT", D(8), "kg", T0 + timedelta(hours=1))
        except RuleViolation as exc:
            return exc.code
        return "OK"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: withdraw(), range(2)))
    assert sorted(results) == ["NEGATIVE_STOCK", "OK"]
    assert call(clean_db, "status", item.id, T0 + timedelta(hours=2))["current_stock"] == D(2)


def test_status_keeps_stock_and_history_in_one_snapshot(clean_db: sessionmaker[Session]) -> None:
    now = T0 + timedelta(days=35)
    item = create(clean_db, "6", T0)
    item_read = Event()
    writer_started = Event()
    writer_finished = Event()

    class PausedStatus(InventoryService):
        def _observation(self, item_id: int) -> Observation | None:
            item_read.set()  # status has already locked and loaded the item row
            assert writer_started.wait(5)
            with pytest.raises(DBAPIError), clean_db.begin() as probe:
                probe.execute(text("SET LOCAL lock_timeout = '100ms'"))
                probe.execute(
                    text("SELECT id FROM items WHERE id = :id FOR UPDATE"), {"id": item_id}
                )
            assert not writer_finished.wait(0.5)  # writer cannot commit before this read ends
            return super()._observation(item_id)

    def read_state() -> dict:
        with clean_db() as session:
            return PausedStatus(session).status(item.id, now)

    def write_exit() -> None:
        assert item_read.wait(5)
        writer_started.set()
        call(clean_db, "add_movement", item.id, "EXIT", D(5), "kg", now, now - timedelta(days=2))
        writer_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        read_future = pool.submit(read_state)
        write_future = pool.submit(write_exit)
        before = read_future.result(timeout=10)
        write_future.result(timeout=10)
    after = call(clean_db, "status", item.id, now)
    assert before["current_stock"] == D(6)
    assert before["consumption"].observed_quantity == 0
    assert before["assessment"].alert_status == "OK"
    assert after["current_stock"] == D(1)
    assert after["consumption"].observed_quantity == D(5)
    assert after["assessment"].alert_status == "LOW_STOCK"


def test_status_refreshes_item_prefetched_for_alert_list(clean_db: sessionmaker[Session]) -> None:
    now = T0 + timedelta(days=35)
    item = create(clean_db, "6", T0)
    with clean_db() as reader:
        service = InventoryService(reader)
        prefetched = service.items()
        assert prefetched[0].current_stock == D(6)
        call(clean_db, "add_movement", item.id, "EXIT", D(5), "kg", now, now - timedelta(days=2))
        state = service.status(item.id, now)
        assert state["current_stock"] == D(1)
        assert state["assessment"].alert_status == "LOW_STOCK"
