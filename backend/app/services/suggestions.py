"""Persisted, immutable calculation snapshots and atomic human purchase decisions."""

from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.procurement_schemas import PricesResponse
from app.api.schemas import ItemStateResponse
from app.domain.errors import RuleViolation
from app.domain.units import amount
from app.persistence.models import Item, Order, PriceQuote, Suggestion, Supplier, SupplierProduct
from app.services.inventory import InventoryService
from app.services.prices import PriceService


class SuggestionService:
    def __init__(self, session: Session, actor_id: int):
        self.session, self.actor_id = session, actor_id

    def get(self, suggestion_id: int) -> Suggestion:
        suggestion = self.session.get(Suggestion, suggestion_id)
        if suggestion is None:
            raise RuleViolation("SUGGESTION_NOT_FOUND", "La sugerencia no existe", 404)
        return suggestion

    def list(self, item_id: int | None = None) -> list[Suggestion]:
        query = select(Suggestion).order_by(Suggestion.id.desc())
        if item_id is not None:
            query = query.where(Suggestion.item_id == item_id)
        return list(self.session.scalars(query))

    def _lock_catalog(self, item_id: int) -> None:
        supplier_ids = select(SupplierProduct.supplier_id).where(SupplierProduct.item_id == item_id)
        self.session.scalars(
            select(Supplier)
            .where(Supplier.id.in_(supplier_ids))
            .order_by(Supplier.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        self.session.scalars(
            select(SupplierProduct)
            .where(SupplierProduct.item_id == item_id)
            .order_by(SupplierProduct.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()

    def generate(self, item_id: int, now: datetime) -> Suggestion:
        with self.session.begin():
            # Same first lock as inventory, approval and receipt. No network under this lock.
            inventory = InventoryService(self.session)
            inventory._item(item_id, lock=True)
            self._lock_catalog(item_id)
            state = inventory.status(item_id, now)
            assessment = state["assessment"]
            if assessment.in_transit_quantity > 0:
                raise RuleViolation("ORDER_IN_TRANSIT", "El insumo tiene una orden en tránsito")
            if assessment.suggested_quantity is None:
                raise RuleViolation("INSUFFICIENT_HISTORY", "No hay historial calculable")
            if not assessment.is_low_stock or assessment.suggested_quantity <= 0:
                raise RuleViolation("NO_PURCHASE_REQUIRED", "No se requiere reposición")
            pending = self.session.scalar(
                select(Suggestion).where(
                    Suggestion.item_id == item_id, Suggestion.status == "PENDING"
                )
            )
            if pending:
                self._validate_approval(pending, now)
                return pending
            prices = PriceService(self.session).catalog(item_id, now)
            comparison = prices["comparison"]
            best = comparison["recommended"]
            if best is None:
                raise RuleViolation(comparison["status"], "Se requieren dos proveedores vigentes")
            if not 1 <= best["packs"] <= 2_147_483_647:
                raise RuleViolation("OUT_OF_RANGE", "Cantidad de formatos fuera de rango", 422)
            products = list(
                self.session.scalars(
                    select(SupplierProduct).where(
                        SupplierProduct.id.in_(
                            [o["supplier_product_id"] for o in comparison["options"]]
                        )
                    )
                )
            )
            snapshot = {
                "version": 1,
                "reference_at": now.isoformat(),
                "inventory": ItemStateResponse.model_validate(
                    {
                        **state,
                        "consumption": asdict(state["consumption"]),
                        "assessment": asdict(state["assessment"]),
                    }
                ).model_dump(mode="json"),
                "prices": PricesResponse.model_validate(prices).model_dump(mode="json"),
                "products": [
                    {
                        "id": p.id,
                        "supplier_id": p.supplier_id,
                        "url": p.url,
                        "pack_quantity": str(p.pack_quantity),
                        "min_packs": p.min_packs,
                        "scrape_config": p.scrape_config,
                    }
                    for p in products
                ],
                "taxes": "VAT_INCLUDED",
                "dispatch": "EXCLUDED",
            }
            suggestion = Suggestion(
                item_id=item_id,
                status="PENDING",
                required_qty=prices["required_quantity"],
                in_transit_qty=assessment.in_transit_quantity,
                supplier_product_id=best["supplier_product_id"],
                packs=best["packs"],
                purchased_qty=amount(Decimal(best["purchased_quantity"])),
                total_clp=amount(Decimal(best["total_clp"])),
                next_best_total_clp=amount(Decimal(comparison["next_best_total_clp"])),
                savings_clp=amount(Decimal(comparison["savings_clp"]), allow_zero=True),
                inputs_snapshot=snapshot,
                created_at=now,
                created_by=self.actor_id,
            )
            self.session.add(suggestion)
            self.session.flush()
            self.session.refresh(suggestion)
        return suggestion

    def decide(
        self, suggestion_id: int, decision: str, reason: str | None, now: datetime
    ) -> Suggestion:
        if now.tzinfo is None or now.utcoffset() is None:
            raise RuleViolation("INVALID_TIMESTAMP", "Se requiere fecha con zona horaria", 422)
        if decision not in {"APPROVE", "REJECT"}:
            raise RuleViolation("INVALID_DECISION", "Use APPROVE o REJECT", 422)
        reason = reason.strip() if reason else None
        if decision == "REJECT" and not reason:
            raise RuleViolation("REJECTION_REASON_REQUIRED", "Indique un motivo de rechazo", 422)
        if decision == "APPROVE" and reason:
            raise RuleViolation("INVALID_DECISION", "La aprobación no lleva motivo de rechazo", 422)
        target = "APPROVED" if decision == "APPROVE" else "REJECTED"
        with self.session.begin():
            item_id = self.session.scalar(
                select(Suggestion.item_id).where(Suggestion.id == suggestion_id)
            )
            if item_id is None:
                raise RuleViolation("SUGGESTION_NOT_FOUND", "La sugerencia no existe", 404)
            self.session.scalar(
                select(Item)
                .where(Item.id == item_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            suggestion = self.session.scalar(
                select(Suggestion)
                .where(Suggestion.id == suggestion_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if suggestion.status != "PENDING":
                if suggestion.status == target and suggestion.rejection_reason == reason:
                    order_id = self.session.scalar(
                        select(Order.id).where(Order.suggestion_id == suggestion.id)
                    )
                    if (target == "APPROVED") != (order_id is not None):
                        raise RuleViolation(
                            "DECISION_STATE_CONFLICT",
                            "La decisión histórica y su orden requieren conciliación",
                        )
                    return suggestion  # Exact retry preserves original actor and time.
                raise RuleViolation("DECISION_CONFLICT", "La sugerencia ya fue decidida")
            if now < suggestion.created_at:
                raise RuleViolation("INVALID_TIMESTAMP", "Decisión anterior a la sugerencia", 422)
            if decision == "APPROVE":
                self._validate_approval(suggestion, now)
                product = self.session.get(SupplierProduct, suggestion.supplier_product_id)
                supplier = self.session.get(Supplier, product.supplier_id)
                try:
                    expected = now + timedelta(days=supplier.lead_time_days)
                except OverflowError as exc:
                    raise RuleViolation(
                        "INVALID_LEAD_TIME", "Fecha de entrega fuera de rango", 422
                    ) from exc
                self.session.add(
                    Order(
                        suggestion_id=suggestion.id,
                        code="OC-" + uuid4().hex[:16].upper(),
                        status="APPROVED",
                        decided_by=self.actor_id,
                        decided_at=now,
                        expected_at=expected,
                    )
                )
            suggestion.status, suggestion.decided_by = target, self.actor_id
            suggestion.decided_at, suggestion.rejection_reason = now, reason
            self.session.flush()
            self.session.refresh(suggestion)
        return suggestion

    def _validate_approval(self, suggestion: Suggestion, now: datetime) -> None:
        self._lock_catalog(suggestion.item_id)
        state = InventoryService(self.session).status(suggestion.item_id, now)
        assessment = state["assessment"]
        if assessment.in_transit_quantity > 0:
            raise RuleViolation("ORDER_IN_TRANSIT", "El insumo tiene una orden en tránsito")
        original = suggestion.inputs_snapshot["inventory"]
        old = original["assessment"]
        comparisons = [
            (state["current_stock"], original["current_stock"]),
            (assessment.safe_stock, old["safe_stock"]),
            (assessment.daily_average, old["daily_average"]),
        ]
        if (
            not assessment.is_low_stock
            or assessment.lead_time_days != old["lead_time_days"]
            or any(a is None or b is None or a != Decimal(b) for a, b in comparisons)
        ):
            raise RuleViolation(
                "STALE_SUGGESTION", "Cambió el inventario; rechace y vuelva a generar"
            )
        # Validate the recommendation and the alternative actually used for frozen savings.
        catalog = PriceService(self.session).catalog(suggestion.item_id, now)
        current_ids = {
            offer["quote_id"] for offer in catalog["offers"] if offer["state"] == "CURRENT"
        }
        comparison = suggestion.inputs_snapshot["prices"]["comparison"]
        best = comparison["recommended"]
        second = next(
            option
            for option in comparison["options"]
            if option["supplier_id"] != best["supplier_id"]
        )
        for option in (best, second):
            quote = self.session.get(PriceQuote, option["quote_id"])
            if quote is None or quote.id not in current_ids:
                raise RuleViolation(
                    "STALE_PRICES", "Los precios cambiaron o vencieron; vuelva a generar"
                )
            product = self.session.get(SupplierProduct, option["supplier_product_id"])
            frozen = next(
                p for p in suggestion.inputs_snapshot["products"] if p["id"] == product.id
            )
            if product.min_packs != frozen["min_packs"]:
                raise RuleViolation("STALE_FORMAT", "Cambió el mínimo de compra; vuelva a generar")
