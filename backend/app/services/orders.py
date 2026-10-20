"""Persisted order history and atomic full receipt; approval remains Pedro's module."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.errors import RuleViolation
from app.domain.units import amount
from app.persistence.models import Item, Movement, Order, Suggestion, SupplierProduct
from app.services.inventory import InventoryService


class OrderService:
    def __init__(self, session: Session, actor_id: int | None = None):
        self.session = session
        self.actor_id = actor_id

    def _view(self, order: Order, suggestion: Suggestion) -> dict:
        receipt = (
            self.session.get(Movement, order.receipt_movement_id)
            if order.receipt_movement_id is not None
            else None
        )
        return {
            "id": order.id,
            "suggestion_id": order.suggestion_id,
            "code": order.code,
            "status": order.status,
            "item_id": suggestion.item_id,
            "supplier_product_id": suggestion.supplier_product_id,
            "packs": suggestion.packs,
            "purchased_qty": suggestion.purchased_qty,
            "total_clp": suggestion.total_clp,
            "decided_by": order.decided_by,
            "decided_at": order.decided_at,
            "expected_at": order.expected_at,
            "received_at": order.received_at,
            "receipt_movement_id": order.receipt_movement_id,
            "received_quantity": receipt.delta if receipt else None,
        }

    def orders(self) -> list[dict]:
        return [
            self._view(order, suggestion)
            for order, suggestion in self.session.execute(
                select(Order, Suggestion)
                .join(Suggestion, Suggestion.id == Order.suggestion_id)
                .order_by(Order.id)
            )
        ]

    def receive(
        self,
        order_id: int,
        now: datetime,
        received_at: datetime | None = None,
        received_quantity: Decimal | None = None,
    ) -> dict:
        with self.session.begin():
            item_id = self.session.scalar(
                select(Suggestion.item_id)
                .join(Order, Order.suggestion_id == Suggestion.id)
                .where(Order.id == order_id)
            )
            if item_id is None:
                raise RuleViolation("ORDER_NOT_FOUND", "Order does not exist", 404)
            # Same first lock as inventory writers/readers: a status snapshot cannot mix the
            # pre-receipt stock with the post-receipt in-transit quantity.
            item = self.session.scalar(
                select(Item)
                .where(Item.id == item_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            order = self.session.scalar(
                select(Order)
                .where(Order.id == order_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            suggestion = self.session.get(Suggestion, order.suggestion_id)
            if order.status == "RECEIVED":
                movement = self.session.get(Movement, order.receipt_movement_id)
                if not movement or movement.item_id != item_id or movement.kind != "ENTRY":
                    raise RuleViolation(
                        "INVALID_RECEIPT_LINK", "Existing order receipt needs reconciliation"
                    )
                if (
                    self.session.scalar(
                        select(Movement.id).where(Movement.invalidates_movement_id == movement.id)
                    )
                    is not None
                ):
                    raise RuleViolation(
                        "RECEIPT_REVERSED", "Invalidated receipt needs order reconciliation"
                    )
                if (received_at is not None and received_at != order.received_at) or (
                    received_quantity is not None and amount(received_quantity) != movement.delta
                ):
                    raise RuleViolation(
                        "RECEIPT_CONFLICT", "Order already received with different data"
                    )
                return self._view(order, suggestion)
            if order.status != "APPROVED" or suggestion.status != "APPROVED":
                raise RuleViolation(
                    "ORDER_NOT_APPROVED", "Only an approved order can enter inventory"
                )
            effective = received_at or now
            if any(value.tzinfo is None or value.utcoffset() is None for value in (now, effective)):
                raise RuleViolation(
                    "INVALID_TIMESTAMP", "Timezone-aware receipt time required", 422
                )
            if not order.decided_at <= effective <= now:
                raise RuleViolation(
                    "INVALID_RECEIPT_DATE", "Receipt must follow approval and not be future", 422
                )
            product = self.session.get(SupplierProduct, suggestion.supplier_product_id)
            if (
                product is None
                or product.item_id != item_id
                or not suggestion.packs
                or suggestion.purchased_qty is None
            ):
                raise RuleViolation(
                    "ORDER_INCOMPLETE", "Approved order needs its persisted product and quantity"
                )
            nominal = amount(suggestion.purchased_qty)
            if nominal != product.pack_quantity * suggestion.packs:
                raise RuleViolation(
                    "ORDER_FORMAT_MISMATCH", "Approved quantity must match full ordered packs"
                )
            variable = bool((product.scrape_config or {}).get("pack", {}).get("is_variable_weight"))
            if variable and received_quantity is None:
                raise RuleViolation(
                    "ACTUAL_WEIGHT_REQUIRED", "Weigh all received variable-weight packs", 422
                )
            quantity = amount(received_quantity) if received_quantity is not None else nominal
            if not variable and quantity != nominal:
                raise RuleViolation(
                    "FIXED_FORMAT_MISMATCH", "Receive all approved fixed-content packs"
                )
            movement = InventoryService(self.session, self.actor_id).add_movement(
                item_id,
                "ENTRY",
                quantity,
                item.base_unit,
                now,
                effective,
                f"Recepción {order.code}",
                _join_transaction=True,
            )
            order.status = "RECEIVED"
            order.received_at = effective
            order.receipt_movement_id = movement.id
            self.session.flush()
            result = self._view(order, suggestion)
        return result
