"""Atomic full OC reception through Pedro's existing inventory, with no order tables."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.domain.errors import RuleViolation
from app.domain.forecast import local_day_start
from app.domain.units import amount
from app.e2.contracts import ApprovedOrder
from app.persistence.models import Item, Movement
from app.services.inventory import InventoryService

PREFIX = "E2_RECEIPT:"
OrderWriter = Callable[[Session, str | int, dict], None]


@dataclass(frozen=True)
class ReceiptResult:
    movement_id: int
    receipt_id: str
    order_id: str | int
    received_quantity: Decimal
    lot: dict
    order_patch: dict
    idempotent_replay: bool


class ReceiptService:
    def __init__(self, session: Session, order_writer: OrderWriter | None = None):
        self.session = session
        self.order_writer = order_writer

    def receive(
        self,
        order: ApprovedOrder,
        receipt_id: str,
        received_at: datetime,
        produced_on: date,
        shelf_life_days: int,
        now: datetime,
        *,
        received_packs: int | None = None,
        actual_received_quantity: Decimal | None = None,
        opened_shelf_life_days: int | None = None,
        piece_quantities: list[Decimal] | None = None,
    ) -> ReceiptResult:
        """Only a trusted order repository supplies approval; this is not an HTTP approval API."""
        if not receipt_id.strip() or len(receipt_id) > 100:
            raise RuleViolation("INVALID_RECEIPT_ID", "Receipt ID is required", 422)
        if order.status == "REJECTED":
            raise RuleViolation("ORDER_NOT_APPROVED", "A rejected order cannot be received")
        if any(value.tzinfo is None or value.utcoffset() is None for value in (received_at, now)):
            raise RuleViolation(
                "INVALID_TIMESTAMP", "Timezone-aware receipt/recording time required", 422
            )
        if not order.approved_at <= received_at <= now:
            raise RuleViolation(
                "INVALID_RECEIPT_DATE", "Receipt must follow approval and not be in the future", 422
            )
        if type(shelf_life_days) is not int or shelf_life_days <= 0:
            raise RuleViolation(
                "INVALID_SHELF_LIFE", "Shelf life must be positive calendar days", 422
            )
        if received_packs is not None and (
            type(received_packs) is not int or received_packs != order.packs
        ):
            raise RuleViolation(
                "PARTIAL_RECEIPT_UNSUPPORTED",
                "v1.1 requires full receipt; confirm partial orders with Pedro",
            )
        nominal_quantity = order.pack.to_pack().quantity_for_packs(order.packs)
        quantity = nominal_quantity
        if order.pack.is_variable_weight:
            if actual_received_quantity is None:
                raise RuleViolation(
                    "ACTUAL_WEIGHT_REQUIRED", "Weigh the complete received formats", 422
                )
            quantity = amount(actual_received_quantity)
        elif (
            actual_received_quantity is not None
            and amount(actual_received_quantity) != nominal_quantity
        ):
            raise RuleViolation(
                "FIXED_FORMAT_MISMATCH", "Fixed net content must match the complete formats"
            )
        if opened_shelf_life_days is not None and (
            type(opened_shelf_life_days) is not int or opened_shelf_life_days <= 0
        ):
            raise RuleViolation(
                "INVALID_OPENED_LIFE", "Use positive calendar days after first opening", 422
            )
        pieces = None
        if piece_quantities is not None:
            pieces = [amount(value) for value in piece_quantities]
            if len(pieces) != order.packs or sum(pieces) != quantity:
                raise RuleViolation(
                    "PIECE_WEIGHT_MISMATCH",
                    "Individual pieces must match full receipt count and mass",
                    422,
                )
            if not order.pack.is_variable_weight and any(
                value != order.pack.to_pack().pack_quantity for value in pieces
            ):
                raise RuleViolation(
                    "FIXED_FORMAT_MISMATCH", "Each fixed piece must retain its declared net content"
                )
        elif opened_shelf_life_days is not None:
            if order.packs == 1:
                pieces = [quantity]
            elif not order.pack.is_variable_weight:
                pieces = [order.pack.to_pack().pack_quantity] * order.packs
            else:
                raise RuleViolation(
                    "PIECE_WEIGHTS_REQUIRED", "Track each variable piece before opening", 422
                )
        with self.session.begin():
            # Global receipt/order identifiers are serialized without adding a competing OC schema.
            keys = sorted(
                int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], "big", signed=True)
                for value in (
                    f"stocksmart-e2-order:{order.id}",
                    f"stocksmart-e2-receipt:{receipt_id}",
                )
            )
            for key in keys:
                self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
            item = self.session.get(Item, order.item_id)
            if item is None:
                raise RuleViolation("ITEM_NOT_FOUND", "Item does not exist", 404)
            if item.base_unit != order.pack.base_unit:
                raise RuleViolation(
                    "INVALID_UNIT", "Order pack and inventory base unit differ", 422
                )
            zone = ZoneInfo(item.timezone_name)
            expected_days = (
                order.expected_at.astimezone(zone).date() - order.issued_at.astimezone(zone).date()
            ).days
            if expected_days != order.lead_time_days:
                raise RuleViolation(
                    "INVALID_EXPECTED_DATE", "Expected receipt must use calendar lead time", 422
                )
            if produced_on > received_at.astimezone(zone).date():
                raise RuleViolation(
                    "INVALID_PRODUCTION_DATE", "Production cannot follow reception", 422
                )
            expires = local_day_start(produced_on + timedelta(days=shelf_life_days), zone)
            if expires <= received_at:
                raise RuleViolation(
                    "EXPIRED_RECEIPT", "Do not receive expired stock into usable inventory"
                )
            metadata = {
                "schema_version": "1.0",
                "receipt_id": receipt_id,
                "order_id": order.id,
                "order_code": order.code,
                "supplier_id": order.supplier_id,
                "supplier_product_id": order.supplier_product_id,
                "item_id": order.item_id,
                "issued_at": order.issued_at.astimezone(UTC).isoformat(),
                "approved_at": order.approved_at.astimezone(UTC).isoformat(),
                "expected_at": order.expected_at.astimezone(UTC).isoformat(),
                "lead_time_days": order.lead_time_days,
                "received_at": received_at.astimezone(UTC).isoformat(),
                "packs_received": order.packs,
                "quantity_received": str(quantity),
                **order.pack.to_pack().as_dict(),
                "lot_id": f"LOT-{receipt_id}",
                "produced_on": produced_on.isoformat(),
                "shelf_life_days": shelf_life_days,
                "expires_at": expires.isoformat(),
            }
            if order.pack.is_variable_weight:
                metadata.update(
                    is_variable_weight=True,
                    nominal_quantity=str(nominal_quantity),
                    quantity_basis="MEASURED_WEIGHT",
                )
            if opened_shelf_life_days is not None:
                metadata.update(
                    opened_shelf_life_days=opened_shelf_life_days,
                    closed_expires_at=expires.isoformat(),
                )
            if pieces is not None:
                metadata["piece_quantities"] = [str(value) for value in pieces]
                metadata["piece_lot_ids"] = [
                    f"LOT-{receipt_id}" if len(pieces) == 1 else f"LOT-{receipt_id}-P{i:02d}"
                    for i in range(1, len(pieces) + 1)
                ]
            canonical = PREFIX + json.dumps(
                metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            previous = self.session.scalars(
                select(Movement).where(Movement.kind == "ENTRY", Movement.note.startswith(PREFIX))
            ).all()
            for movement in previous:
                try:
                    saved = json.loads(movement.note[len(PREFIX) :])
                except ValueError as exc:
                    raise RuleViolation(
                        "INVALID_RECEIPT_METADATA", "Existing receipt metadata needs review"
                    ) from exc
                if saved["receipt_id"] == receipt_id:
                    if movement.note != canonical:
                        raise RuleViolation(
                            "RECEIPT_CONFLICT", "Receipt ID reused with different data"
                        )
                    if (
                        self.session.scalar(
                            select(Movement.id).where(
                                Movement.kind == "REVERSAL",
                                Movement.invalidates_movement_id == movement.id,
                            )
                        )
                        is not None
                    ):
                        raise RuleViolation(
                            "RECEIPT_REVERSED", "Reversed reception requires order reconciliation"
                        )
                    return self._result(movement, saved, True)
                if saved["order_id"] == order.id:
                    raise RuleViolation(
                        "ORDER_ALREADY_RECEIVED", "This order already has a full reception"
                    )
            if order.status != "APPROVED":
                raise RuleViolation(
                    "ORDER_NOT_APPROVED", "Only an approved order can enter inventory"
                )
            movement = InventoryService(self.session).add_movement(
                order.item_id,
                "ENTRY",
                quantity,
                order.pack.base_unit,
                now,
                received_at,
                canonical,
                _join_transaction=True,
            )
            return self._result(movement, metadata, False)

    def _result(self, movement: Movement, metadata: dict, replay: bool) -> ReceiptResult:
        patch = {
            "status": "RECEIVED",
            "received_at": datetime.fromisoformat(metadata["received_at"]),
            "receipt_movement_id": movement.id,
        }
        if metadata.get("is_variable_weight"):
            patch["received_quantity"] = Decimal(metadata["quantity_received"])
        if self.order_writer:
            # The callback updates Pedro's OC using THIS session; it must not commit independently.
            self.order_writer(self.session, metadata["order_id"], patch)
        lot = {
            key: metadata[key]
            for key in (
                "lot_id",
                "receipt_id",
                "order_id",
                "supplier_id",
                "supplier_product_id",
                "item_id",
                "received_at",
                "produced_on",
                "shelf_life_days",
                "expires_at",
                "quantity_received",
                "base_unit",
            )
        }
        for key in (
            "is_variable_weight",
            "nominal_quantity",
            "quantity_basis",
            "opened_shelf_life_days",
            "closed_expires_at",
            "piece_quantities",
            "piece_lot_ids",
        ):
            if key in metadata:
                lot[key] = metadata[key]
        return ReceiptResult(
            movement.id,
            metadata["receipt_id"],
            metadata["order_id"],
            Decimal(metadata["quantity_received"]),
            lot,
            patch,
            replay,
        )
