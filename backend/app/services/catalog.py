"""Supplier/product management on Pedro's procurement tables, without deleting history."""

from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.procurement_schemas import ScrapeConfig
from app.domain.commercial import CommercialPack
from app.domain.errors import RuleViolation
from app.domain.units import amount
from app.persistence.models import (
    Item,
    PriceQuote,
    ScrapeRun,
    Suggestion,
    Supplier,
    SupplierProduct,
)
from scraper.contracts import ScrapeError
from scraper.normalize import validate_public_product_url


def _public_url(value: str) -> None:
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise RuleViolation("INVALID_SOURCE", "Use a valid public HTTPS URL", 422) from exc
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise RuleViolation("INVALID_SOURCE", "Use a public HTTPS URL without credentials", 422)


def _name(value: str | None) -> str:
    if value is None or not value.strip():
        raise RuleViolation("INVALID_NAME", "Name cannot be blank", 422)
    return value.strip()


class CatalogService:
    def __init__(self, session: Session):
        self.session = session

    def supplier(self, supplier_id: int, *, lock: bool = False) -> Supplier:
        query = select(Supplier).where(Supplier.id == supplier_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        supplier = self.session.scalar(query)
        if supplier is None:
            raise RuleViolation("SUPPLIER_NOT_FOUND", "Supplier does not exist", 404)
        return supplier

    def suppliers(self) -> list[Supplier]:
        return list(self.session.scalars(select(Supplier).order_by(Supplier.id)))

    def create_supplier(self, data: dict, now: datetime) -> Supplier:
        data = {**data, "name": _name(data["name"])}
        if data.get("price_source_url") is not None:
            _public_url(data["price_source_url"])
        try:
            with self.session.begin():
                supplier = Supplier(**data, created_at=now, updated_at=now)
                self.session.add(supplier)
                self.session.flush()
        except IntegrityError as exc:
            raise RuleViolation("SUPPLIER_EXISTS", "Supplier name is already registered") from exc
        return supplier

    def update_supplier(self, supplier_id: int, changes: dict, now: datetime) -> Supplier:
        if not changes:
            raise RuleViolation("EMPTY_UPDATE", "Send at least one field to change", 422)
        if any(changes.get(key, True) is None for key in ("lead_time_days", "is_active")):
            raise RuleViolation(
                "INVALID_UPDATE", "Delivery time and active flag cannot be null", 422
            )
        if "name" in changes:
            changes["name"] = _name(changes["name"])
        if changes.get("price_source_url") is not None:
            _public_url(changes["price_source_url"])
        try:
            with self.session.begin():
                supplier = self.supplier(supplier_id, lock=True)
                if "adapter" in changes and changes["adapter"] != supplier.adapter:
                    # Never silently reinterpret already configured public URLs with another parser.
                    if (
                        self.session.scalar(
                            select(SupplierProduct.id).where(
                                SupplierProduct.supplier_id == supplier_id
                            )
                        )
                        is not None
                    ):
                        raise RuleViolation("ADAPTER_IN_USE", "Existing products use this adapter")
                for key, value in changes.items():
                    setattr(supplier, key, value)
                supplier.updated_at = now
                self.session.flush()
        except IntegrityError as exc:
            raise RuleViolation("SUPPLIER_EXISTS", "Supplier name is already registered") from exc
        return supplier

    def products(self, supplier_id: int) -> list[SupplierProduct]:
        self.supplier(supplier_id)
        return list(
            self.session.scalars(
                select(SupplierProduct)
                .where(SupplierProduct.supplier_id == supplier_id)
                .order_by(SupplierProduct.id)
            )
        )

    def _validate_product(self, supplier: Supplier, data: dict) -> None:
        item = self.session.get(Item, data["item_id"])
        if item is None:
            raise RuleViolation("ITEM_NOT_FOUND", "Item does not exist", 404)
        data["name_on_site"] = _name(data["name_on_site"])
        data["pack_quantity"] = amount(data["pack_quantity"])
        CommercialPack(
            data["name_on_site"], data["pack_quantity"], item.base_unit, 1, item.base_unit
        )
        _public_url(data["url"])
        if supplier.adapter is not None:
            try:
                validate_public_product_url(supplier.adapter, data["url"])
            except (ScrapeError, ValueError) as exc:
                raise RuleViolation(
                    "INVALID_SOURCE", "Use the adapter's public product page", 422
                ) from exc
        if data.get("scrape_config") is not None:
            if supplier.adapter is None:
                raise RuleViolation(
                    "ADAPTER_REQUIRED", "Configure a validated supplier adapter", 422
                )
            config = ScrapeConfig.model_validate(data["scrape_config"])
            if (
                config.pack.base_unit != item.base_unit
                or config.pack.to_pack().pack_quantity != data["pack_quantity"]
            ):
                raise RuleViolation(
                    "FORMAT_MISMATCH", "Pack must match the item's base unit and quantity", 422
                )
            data["scrape_config"] = config.model_dump(mode="json")

    def create_product(self, supplier_id: int, data: dict) -> SupplierProduct:
        try:
            with self.session.begin():
                supplier = self.supplier(supplier_id, lock=True)
                self._validate_product(supplier, data)
                product = SupplierProduct(supplier_id=supplier_id, **data)
                self.session.add(product)
                self.session.flush()
        except IntegrityError as exc:
            raise RuleViolation(
                "PRODUCT_EXISTS", "Product URL is already registered for this supplier"
            ) from exc
        return product

    def update_product(self, supplier_id: int, product_id: int, changes: dict) -> SupplierProduct:
        if not changes:
            raise RuleViolation("EMPTY_UPDATE", "Send at least one field to change", 422)
        if any(value is None for key, value in changes.items() if key != "scrape_config"):
            raise RuleViolation("INVALID_UPDATE", "Product fields cannot be null", 422)
        try:
            with self.session.begin():
                supplier = self.supplier(supplier_id, lock=True)
                product = self.session.scalar(
                    select(SupplierProduct)
                    .where(
                        SupplierProduct.id == product_id, SupplierProduct.supplier_id == supplier_id
                    )
                    .with_for_update()
                )
                if product is None:
                    raise RuleViolation("PRODUCT_NOT_FOUND", "Supplier product does not exist", 404)
                data = {
                    key: getattr(product, key)
                    for key in (
                        "item_id",
                        "name_on_site",
                        "url",
                        "pack_quantity",
                        "min_packs",
                        "is_active",
                        "scrape_config",
                    )
                }
                data.update(changes)
                self._validate_product(supplier, data)
                identity = {"url", "pack_quantity", "scrape_config"}
                if any(data[key] != getattr(product, key) for key in identity):
                    used = self.session.scalar(
                        select(PriceQuote.id)
                        .where(PriceQuote.supplier_product_id == product_id)
                        .limit(1)
                    ) or self.session.scalar(
                        select(Suggestion.id)
                        .where(Suggestion.supplier_product_id == product_id)
                        .limit(1)
                    )
                    if used:
                        raise RuleViolation(
                            "PRODUCT_IN_USE",
                            "Preserve its history; deactivate and register a new product",
                        )
                for key, value in data.items():
                    setattr(product, key, value)
                self.session.flush()
        except IntegrityError as exc:
            raise RuleViolation(
                "PRODUCT_EXISTS", "Product URL is already registered for this supplier"
            ) from exc
        return product

    def runs(self, supplier_id: int) -> list[ScrapeRun]:
        self.supplier(supplier_id)
        return list(
            self.session.scalars(
                select(ScrapeRun)
                .where(ScrapeRun.supplier_id == supplier_id)
                .order_by(ScrapeRun.id.desc())
            )
        )
