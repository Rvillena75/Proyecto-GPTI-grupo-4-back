"""Transactional inventory use cases. All writes lock the item's PostgreSQL row."""

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.errors import RuleViolation
from app.domain.forecast import Consumption, assess_stock, calculate_consumption
from app.domain.history import CountFact, MovementFact, effective_movements, validate_history
from app.domain.shortage import calculate_count, shortage_rate
from app.domain.units import BASE_UNITS, amount, to_base
from app.persistence.models import Count, Item, Movement, Observation


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RuleViolation("INVALID_TIMESTAMP", "Timezone-aware timestamp required", 422)


class InventoryService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _item(self, item_id: int, *, lock: bool = False) -> Item:
        query = select(Item).where(Item.id == item_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        item = self.session.scalar(query)
        if item is None:
            raise RuleViolation("ITEM_NOT_FOUND", "Item does not exist", 404)
        return item

    def _observation(self, item_id: int) -> Observation | None:
        return self.session.scalar(
            select(Observation).where(Observation.item_id == item_id, Observation.is_active)
        )

    def _require_observation(self, item_id: int) -> Observation:
        observation = self._observation(item_id)
        if observation is None:
            raise RuleViolation("INITIAL_STOCK_REQUIRED", "Active initial stock is required")
        return observation

    def _facts(self, observation_id: int) -> tuple[list[MovementFact], list[CountFact]]:
        movements = self.session.scalars(
            select(Movement).where(Movement.observation_id == observation_id)
        ).all()
        counts = self.session.scalars(
            select(Count).where(Count.observation_id == observation_id)
        ).all()
        return (
            [
                MovementFact(
                    m.id, m.kind, m.delta, m.occurred_at, m.created_at, m.invalidates_movement_id
                )
                for m in movements
            ],
            [
                CountFact(c.adjustment_movement_id, c.theoretical_before, c.physical_stock)
                for c in counts
            ],
        )

    def _validate(self, item: Item, observation: Observation) -> None:
        movements, counts = self._facts(observation.id)
        effective_stock = validate_history(movements, counts)
        if effective_stock != item.current_stock:
            raise RuleViolation("STOCK_LEDGER_MISMATCH", "Current stock differs from the ledger")

    def _occurred_at(
        self, occurred_at: datetime | None, observation: Observation, now: datetime
    ) -> datetime:
        value = occurred_at or now
        _aware(value)
        if value < observation.started_at:
            raise RuleViolation(
                "BEFORE_INITIAL_STOCK", "Movement cannot precede active initial stock", 422
            )
        if value > now:
            raise RuleViolation("FUTURE_MOVEMENT", "Movement cannot occur in the future", 422)
        return value

    def _new_initial(
        self, item: Item, quantity: Decimal, unit: str, now: datetime
    ) -> tuple[Observation, Movement]:
        stock = to_base(quantity, unit, item.base_unit, allow_zero=True)
        observation = Observation(item_id=item.id, started_at=now, is_active=True)
        self.session.add(observation)
        self.session.flush()
        initial = Movement(
            item_id=item.id,
            observation_id=observation.id,
            kind="INITIAL",
            delta=stock,
            input_quantity=quantity,
            input_unit=unit,
            occurred_at=now,
            created_at=now,
        )
        self.session.add(initial)
        item.current_stock = stock
        item.updated_at = now
        self.session.flush()
        self._validate(item, observation)
        return observation, initial

    def create_item(
        self,
        name: str,
        base_unit: str,
        initial_quantity: Decimal,
        initial_unit: str,
        timezone_name: str,
        manual_safe_stock: Decimal | None,
        now: datetime,
    ) -> Item:
        _aware(now)
        if base_unit not in BASE_UNITS:
            raise RuleViolation("INVALID_UNIT", "Base unit must be kg, L or unidad", 422)
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise RuleViolation("INVALID_TIMEZONE", "Unknown IANA timezone", 422) from exc
        if not name.strip():
            raise RuleViolation("INVALID_NAME", "Item name cannot be blank", 422)
        safe = amount(manual_safe_stock, allow_zero=True) if manual_safe_stock is not None else None
        with self.session.begin():
            item = Item(
                name=name.strip(),
                base_unit=base_unit,
                timezone_name=timezone_name,
                current_stock=Decimal(0),
                manual_safe_stock=safe,
                created_at=now,
                updated_at=now,
            )
            self.session.add(item)
            self.session.flush()
            self._new_initial(item, initial_quantity, initial_unit, now)
        return item

    def replace_initial(
        self, item_id: int, quantity: Decimal, unit: str, now: datetime
    ) -> Movement:
        _aware(now)
        with self.session.begin():
            item = self._item(item_id, lock=True)
            if self._observation(item_id) is not None:
                raise RuleViolation(
                    "INITIAL_STOCK_ALREADY_EXISTS", "Initial stock is already active"
                )
            if item.current_stock != 0:
                raise RuleViolation(
                    "STOCK_LEDGER_MISMATCH", "Inactive inventory must have zero stock"
                )
            _, initial = self._new_initial(item, quantity, unit, now)
        return initial

    def add_movement(
        self,
        item_id: int,
        kind: str,
        quantity: Decimal,
        unit: str,
        now: datetime,
        occurred_at: datetime | None = None,
        note: str | None = None,
    ) -> Movement:
        _aware(now)
        if kind not in {"ENTRY", "EXIT"}:
            raise RuleViolation("INVALID_MOVEMENT_KIND", "Only ENTRY or EXIT is accepted", 422)
        with self.session.begin():
            item = self._item(item_id, lock=True)
            observation = self._require_observation(item_id)
            effective_at = self._occurred_at(occurred_at, observation, now)
            base_quantity = to_base(quantity, unit, item.base_unit)
            delta = base_quantity if kind == "ENTRY" else -base_quantity
            if item.current_stock + delta < 0:
                raise RuleViolation("NEGATIVE_STOCK", "Operation would leave negative stock")
            amount(item.current_stock + delta, allow_zero=True)
            movement = Movement(
                item_id=item_id,
                observation_id=observation.id,
                kind=kind,
                delta=delta,
                input_quantity=quantity,
                input_unit=unit,
                occurred_at=effective_at,
                created_at=now,
                note=note,
            )
            self.session.add(movement)
            item.current_stock += delta
            item.updated_at = now
            self.session.flush()
            self._validate(item, observation)
        return movement

    def _original(self, movement_id: int, observation_id: int) -> Movement:
        original = self.session.get(Movement, movement_id)
        if original is None or original.observation_id != observation_id:
            raise RuleViolation("MOVEMENT_NOT_FOUND", "Movement does not exist", 404)
        if original.kind in {"REVERSAL", "CORRECTION"}:
            raise RuleViolation("INVALID_CORRECTION_TARGET", "Cannot correct a correction")
        already = self.session.scalar(
            select(Movement.id).where(Movement.invalidates_movement_id == movement_id)
        )
        if already is not None:
            raise RuleViolation("ALREADY_CORRECTED", "Movement already has a correction")
        return original

    def reverse(
        self, movement_id: int, now: datetime, occurred_at: datetime | None = None
    ) -> Movement:
        _aware(now)
        with self.session.begin():
            original_hint = self.session.get(Movement, movement_id)
            if original_hint is None:
                raise RuleViolation("MOVEMENT_NOT_FOUND", "Movement does not exist", 404)
            item = self._item(original_hint.item_id, lock=True)
            observation = self._require_observation(item.id)
            original = self._original(movement_id, observation.id)
            effective_at = self._occurred_at(occurred_at, observation, now)
            if effective_at < original.occurred_at:
                raise RuleViolation(
                    "INVALID_REVERSAL_TIME", "Reversal cannot predate its target", 422
                )
            if original.kind == "INITIAL":
                movements, _ = self._facts(observation.id)
                reversed_ids = {
                    m.invalidates_movement_id for m in movements if m.kind == "REVERSAL"
                }
                active_later = any(
                    m.kind not in {"REVERSAL", "CORRECTION", "INITIAL"} and m.id not in reversed_ids
                    for m in movements
                )
                if active_later:
                    raise RuleViolation(
                        "INITIAL_STOCK_HAS_DEPENDENT_MOVEMENTS",
                        "Initial stock has active dependent movements",
                    )
            delta = -original.delta
            if item.current_stock + delta < 0:
                raise RuleViolation("NEGATIVE_STOCK", "Reversal would leave negative stock")
            amount(item.current_stock + delta, allow_zero=True)
            reversal = Movement(
                item_id=item.id,
                observation_id=observation.id,
                kind="REVERSAL",
                delta=delta,
                occurred_at=effective_at,
                created_at=now,
                invalidates_movement_id=original.id,
            )
            self.session.add(reversal)
            item.current_stock += delta
            item.updated_at = now
            self.session.flush()
            self._validate(item, observation)
            if original.kind == "INITIAL":
                observation.is_active = False
        return reversal

    def record_correction(
        self, movement_id: int, note: str, now: datetime, stock_effect: Decimal = Decimal(0)
    ) -> Movement:
        """D6: apply only the documented stock effect needed from now onward."""
        _aware(now)
        if not note.strip():
            raise RuleViolation("CORRECTION_NOTE_REQUIRED", "Explain the correction", 422)
        magnitude = amount(abs(stock_effect), allow_zero=True)
        delta = -magnitude if stock_effect < 0 else magnitude
        with self.session.begin():
            original_hint = self.session.get(Movement, movement_id)
            if original_hint is None:
                raise RuleViolation("MOVEMENT_NOT_FOUND", "Movement does not exist", 404)
            item = self._item(original_hint.item_id, lock=True)
            observation = self._require_observation(item.id)
            original = self._original(movement_id, observation.id)
            if original.kind == "INITIAL":
                raise RuleViolation(
                    "INITIAL_STOCK_HAS_DEPENDENT_MOVEMENTS",
                    "Correct initial stock through a physical count",
                )
            candidate_counts = self.session.scalars(
                select(Count).where(
                    Count.observation_id == observation.id,
                    Count.occurred_at >= original.occurred_at,
                )
            ).all()
            original_order = (original.occurred_at, original.created_at, original.id)
            has_later_count = any(
                (count.occurred_at, count.created_at, count.adjustment_movement_id) > original_order
                for count in candidate_counts
            )
            if not has_later_count:
                raise RuleViolation(
                    "PHYSICAL_COUNT_REQUIRED",
                    "Correction of a reconciled error requires a later physical count",
                )
            if item.current_stock + delta < 0:
                raise RuleViolation("NEGATIVE_STOCK", "Correction would leave negative stock")
            amount(item.current_stock + delta, allow_zero=True)
            correction = Movement(
                item_id=item.id,
                observation_id=observation.id,
                kind="CORRECTION",
                delta=delta,
                occurred_at=now,
                created_at=now,
                invalidates_movement_id=original.id,
                note=note.strip(),
            )
            self.session.add(correction)
            item.current_stock += delta
            item.updated_at = now
            self.session.flush()
            self._validate(item, observation)
        return correction

    def record_count(
        self,
        item_id: int,
        physical_quantity: Decimal,
        unit: str,
        now: datetime,
        cause: str | None = None,
        corrects_movement_id: int | None = None,
    ) -> Count:
        _aware(now)
        with self.session.begin():
            item = self._item(item_id, lock=True)
            observation = self._require_observation(item_id)
            physical = to_base(physical_quantity, unit, item.base_unit, allow_zero=True)
            if corrects_movement_id is not None:
                original = self._original(corrects_movement_id, observation.id)
                if original.kind == "INITIAL":
                    # A count corrects its balance, but never invalidates its observation start.
                    pass
                else:
                    self.session.add(
                        Movement(
                            item_id=item.id,
                            observation_id=observation.id,
                            kind="CORRECTION",
                            delta=Decimal(0),
                            occurred_at=now,
                            created_at=now,
                            invalidates_movement_id=original.id,
                            note="Corrected by physical count",
                        )
                    )
            calculated = calculate_count(item.current_stock, physical)
            adjustment = Movement(
                item_id=item.id,
                observation_id=observation.id,
                kind="COUNT_ADJUSTMENT",
                delta=calculated.difference,
                occurred_at=now,
                created_at=now,
            )
            self.session.add(adjustment)
            self.session.flush()
            count = Count(
                item_id=item.id,
                observation_id=observation.id,
                adjustment_movement_id=adjustment.id,
                corrects_movement_id=corrects_movement_id,
                theoretical_before=item.current_stock,
                physical_stock=physical,
                difference=calculated.difference,
                shortage=calculated.shortage,
                cause=cause,
                occurred_at=now,
                created_at=now,
            )
            self.session.add(count)
            item.current_stock = physical
            item.updated_at = now
            self.session.flush()
            self._validate(item, observation)
        return count

    def set_manual_safe_stock(self, item_id: int, value: Decimal | None, now: datetime) -> Item:
        _aware(now)
        safe = amount(value, allow_zero=True) if value is not None else None
        with self.session.begin():
            item = self._item(item_id, lock=True)
            item.manual_safe_stock = safe
            item.updated_at = now
        return item

    def status(self, item_id: int, now: datetime) -> dict:
        _aware(now)
        # Writers lock this same row first. Hold the read lock until the request session closes,
        # so the balance and every ledger query describe one coherent item state.
        item = self._item(item_id, lock=True)
        observation = self._observation(item_id)
        if observation is None:
            consumption = Consumption("INSUFFICIENT_HISTORY", 0, Decimal(0), None, None, None)
        else:
            movements, _ = self._facts(observation.id)
            metric_invalidated = {
                m.invalidates_movement_id for m in movements if m.kind in {"REVERSAL", "CORRECTION"}
            }
            exits = [
                (m.occurred_at, -m.delta)
                for m in movements
                if m.kind == "EXIT" and m.id not in metric_invalidated
            ]
            consumption = calculate_consumption(
                observation.started_at, now, item.timezone_name, exits
            )
        assessment = assess_stock(item.current_stock, item.manual_safe_stock, consumption, now)
        return {
            "id": item.id,
            "name": item.name,
            "base_unit": item.base_unit,
            "timezone": item.timezone_name,
            "current_stock": item.current_stock,
            "manual_safe_stock": item.manual_safe_stock,
            "observation_started_at": observation.started_at if observation else None,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "consumption": consumption,
            "assessment": assessment,
        }

    def shortage_rate(self, item_id: int, start: datetime, end: datetime) -> dict:
        _aware(start)
        _aware(end)
        if start >= end:
            raise RuleViolation("INVALID_PERIOD", "Start must precede end", 422)
        self._item(item_id, lock=True)
        observation = self._observation(item_id)
        if observation is None or start < observation.started_at:
            return {
                "status": "NOT_CALCULABLE",
                "available": None,
                "shortage": Decimal(0),
                "rate_percent": None,
            }
        movements, _ = self._facts(observation.id)
        counts = self.session.scalars(
            select(Count).where(Count.observation_id == observation.id)
        ).all()
        invalidated_for_metrics = {
            m.invalidates_movement_id for m in movements if m.kind in {"REVERSAL", "CORRECTION"}
        }
        timeline = effective_movements(movements)
        stock_at_start = sum(
            (
                m.delta
                for m in timeline
                if m.occurred_at < start or (m.kind == "INITIAL" and m.occurred_at == start)
            ),
            Decimal(0),
        )
        entries = sum(
            (
                m.delta
                for m in movements
                if m.kind == "ENTRY"
                and m.id not in invalidated_for_metrics
                and start <= m.occurred_at < end
            ),
            Decimal(0),
        )
        exits = sum(
            (
                -m.delta
                for m in movements
                if m.kind == "EXIT"
                and m.id not in invalidated_for_metrics
                and start <= m.occurred_at < end
            ),
            Decimal(0),
        )
        positive = sum(
            (
                count.difference
                for count in counts
                if count.adjustment_movement_id not in invalidated_for_metrics
                and count.difference > 0
                and start <= count.occurred_at < end
            ),
            Decimal(0),
        )
        shortages = sum(
            (
                count.shortage
                for count in counts
                if count.adjustment_movement_id not in invalidated_for_metrics
                and start <= count.occurred_at < end
            ),
            Decimal(0),
        )
        result = shortage_rate(stock_at_start, entries, positive, shortages)
        available = stock_at_start + entries + positive
        stock_at_end = sum(
            (m.delta for m in timeline if m.occurred_at < end),
            Decimal(0),
        )
        reconstructed_end = stock_at_start + entries - exits + positive - shortages
        if stock_at_end != reconstructed_end:
            # A later documentary correction can invalidate an event whose effect was already
            # absorbed by a count. The unchanged count stays auditable, but its rate denominator
            # cannot be reconstructed reliably from currently valid economic events alone.
            return {
                "status": "NOT_CALCULABLE",
                "available": None,
                "shortage": shortages,
                "rate_percent": None,
            }
        return {
            "status": result.status,
            "available": available if result.value is not None else None,
            "shortage": shortages,
            "rate_percent": result.value,
        }

    def movements(self, item_id: int) -> list[Movement]:
        self._item(item_id)
        return list(
            self.session.scalars(
                select(Movement).where(Movement.item_id == item_id).order_by(Movement.id)
            ).all()
        )

    def counts(self, item_id: int) -> list[Count]:
        self._item(item_id)
        return list(
            self.session.scalars(
                select(Count).where(Count.item_id == item_id).order_by(Count.id)
            ).all()
        )

    def items(self) -> list[Item]:
        return list(self.session.scalars(select(Item).order_by(Item.id)).all())
