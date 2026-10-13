"""Persist the existing public adapters' results in Pedro's price/run tables."""

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.domain.errors import RuleViolation
from app.domain.units import SCALE, amount
from app.persistence.models import Item, PriceQuote, ScrapeRun, Supplier, SupplierProduct
from app.services.inventory import InventoryService
from scraper.adaptadores import ADAPTERS
from scraper.contracts import ScrapeError
from scraper.normalize import normalize, validate_identity
from scraper.pipeline import compare_offers
from scraper.store import quote_state


def _configuration(supplier: Supplier, product: SupplierProduct, item: Item) -> dict:
    metadata = product.scrape_config
    if supplier.adapter not in ADAPTERS or not metadata:
        raise ScrapeError(
            "SCRAPE_CONFIG_REQUIRED", "Configure the validated adapter and product metadata"
        )
    return {
        **metadata,
        "supplier_id": supplier.id,
        "supplier_product_id": product.id,
        "item_id": item.id,
        "name": supplier.name,
        "adapter": supplier.adapter,
        "url": product.url,
        "name_on_site": product.name_on_site,
        "min_packs": product.min_packs,
        "is_active": supplier.is_active and product.is_active,
        "shipping_cost_clp": "0",  # C4: dispatch is outside the MVP.
    }


class PriceService:
    def __init__(self, session: Session):
        self.session = session

    def _availability(self, product_id: int, supplier_id: int) -> tuple[datetime, bool] | None:
        observations = []
        for run in self.session.scalars(
            select(ScrapeRun).where(ScrapeRun.supplier_id == supplier_id)
        ):
            try:
                entries = json.loads(run.message or "{}").get("products", [])
            except (ValueError, AttributeError):
                continue  # Older human-readable run messages carry no availability observation.
            for result in entries:
                if result["supplier_product_id"] == product_id and result["status"] in {
                    "OK",
                    "OUT_OF_STOCK",
                }:
                    observations.append(
                        (datetime.fromisoformat(result["observed_at"]), result["status"] == "OK")
                    )
        return max(observations) if observations else None

    def catalog(self, item_id: int, now: datetime) -> dict:
        state = InventoryService(self.session).status(item_id, now)
        required = state["assessment"].suggested_quantity
        if required is not None:
            # Forecast division may be recurring; buying whole formats still rounds upward.
            required = amount(required.quantize(SCALE, rounding=ROUND_CEILING), allow_zero=True)
        offers, current, configurations = [], [], []
        rows = self.session.execute(
            select(SupplierProduct, Supplier)
            .join(Supplier, Supplier.id == SupplierProduct.supplier_id)
            .where(SupplierProduct.item_id == item_id)
            .order_by(SupplierProduct.id)
        ).all()
        for product, supplier in rows:
            quote = self.session.scalar(
                select(PriceQuote)
                .where(PriceQuote.supplier_product_id == product.id)
                .order_by(PriceQuote.observed_at.desc(), PriceQuote.id.desc())
                .limit(1)
            )
            normalized = (
                None
                if quote is None
                else {
                    "id": quote.id,
                    "supplier_id": supplier.id,
                    "supplier_product_id": product.id,
                    "pack_price_clp": str(quote.pack_price_clp),
                    "available": quote.available,
                    "observed_at": quote.observed_at.isoformat(),
                    "valid_until": quote.valid_until.isoformat(),
                }
            )
            price_state = quote_state(normalized, now)
            latest = self._availability(product.id, supplier.id)
            if (
                latest
                and latest[0] <= now
                and not latest[1]
                and (quote is None or latest[0] >= quote.observed_at)
            ):
                price_state = "OUT_OF_STOCK"
            if not supplier.is_active or not product.is_active:
                price_state = "INACTIVE"
            offers.append(
                {
                    "supplier_id": supplier.id,
                    "supplier": supplier.name,
                    "supplier_product_id": product.id,
                    "quote_id": quote.id if quote else None,
                    "pack_quantity": product.pack_quantity,
                    "pack_price_clp": quote.pack_price_clp if quote else None,
                    "unit_price_clp": quote.unit_price_clp if quote else None,
                    "available": False
                    if price_state == "OUT_OF_STOCK"
                    else (quote.available if quote else None),
                    "observed_at": quote.observed_at if quote else None,
                    "valid_until": quote.valid_until if quote else None,
                    "state": price_state,
                }
            )
            # Compare frozen ORM packs; historical quotes need no live scraper config.
            configurations.append(
                {
                    "supplier_id": supplier.id,
                    "supplier_product_id": product.id,
                    "name": supplier.name,
                    "min_packs": product.min_packs,
                    "is_active": supplier.is_active and product.is_active,
                    "shipping_cost_clp": "0",
                    "pack": {
                        "commercial_format": product.name_on_site,
                        "content_quantity": str(product.pack_quantity),
                        "content_unit": state["base_unit"],
                        "units_per_pack": 1,
                        "base_unit": state["base_unit"],
                    },
                }
            )
            if price_state == "CURRENT":
                current.append(normalized)
        comparison = (
            compare_offers(current, configurations, required)
            if required is not None
            else {"status": "INSUFFICIENT_HISTORY", "options": [], "recommended": None}
        )
        return {
            "item_id": item_id,
            "required_quantity": required,
            "offers": offers,
            "comparison": comparison,
        }

    def _record_quote(self, quote: dict, run: ScrapeRun, config: dict) -> None:
        """Only normalized adapter results reach this private persistence boundary."""
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": config["supplier_product_id"]},
        )
        supplier = self.session.scalar(
            select(Supplier)
            .where(Supplier.id == config["supplier_id"])
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        product = self.session.scalar(
            select(SupplierProduct)
            .where(SupplierProduct.id == config["supplier_product_id"])
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if not product or not supplier or not product.is_active or not supplier.is_active:
            raise ScrapeError("CONFIGURATION_CHANGED", "Product or supplier was deactivated")
        item = self.session.get(Item, product.item_id)
        if _configuration(supplier, product, item) != config:
            raise ScrapeError(
                "CONFIGURATION_CHANGED", "Configuration changed during the public request"
            )
        observed = datetime.fromisoformat(quote["observed_at"])
        valid_until = datetime.fromisoformat(quote["valid_until"])
        if valid_until != observed + timedelta(days=7):
            raise ScrapeError("INVALID_VALIDITY", "Prices retain their original seven-day validity")
        price = amount(Decimal(quote["pack_price_clp"]))
        unit_price = amount((price / product.pack_quantity).quantize(SCALE, rounding=ROUND_HALF_UP))
        existing = self.session.scalar(
            select(PriceQuote).where(
                PriceQuote.supplier_product_id == product.id,
                PriceQuote.observed_at == observed,
            )
        )
        if existing:
            if (
                existing.pack_price_clp,
                existing.unit_price_clp,
                existing.available,
                existing.valid_until,
            ) != (price, unit_price, quote["available"], valid_until):
                raise ScrapeError("QUOTE_CONFLICT", "Original observation cannot be overwritten")
            return
        self.session.add(
            PriceQuote(
                supplier_product_id=product.id,
                run_id=run.id,
                pack_price_clp=price,
                unit_price_clp=unit_price,
                available=quote["available"],
                observed_at=observed,
                valid_until=valid_until,
            )
        )
        self.session.flush()

    def refresh(self, item_id: int, now: datetime, transport) -> dict:
        groups = defaultdict(list)
        with self.session.begin():
            item = self.session.get(Item, item_id)
            if item is None:
                raise RuleViolation("ITEM_NOT_FOUND", "Item does not exist", 404)
            for product, supplier in self.session.execute(
                select(SupplierProduct, Supplier)
                .join(Supplier, Supplier.id == SupplierProduct.supplier_id)
                .where(
                    SupplierProduct.item_id == item_id,
                    SupplierProduct.is_active,
                    Supplier.is_active,
                )
                .order_by(SupplierProduct.id)
            ):
                try:
                    config = _configuration(supplier, product, item)
                except ScrapeError:
                    config = None
                groups[supplier.id].append((product.id, config))
        runs = []
        for supplier_id, products in groups.items():
            with self.session.begin():
                run = ScrapeRun(
                    supplier_id=supplier_id, started_at=datetime.now(UTC), status="RUNNING"
                )
                self.session.add(run)
                self.session.flush()
            results = []
            for product_id, config in products:
                result = {"supplier_product_id": product_id}
                try:
                    if config is None:
                        raise ScrapeError(
                            "SCRAPE_CONFIG_REQUIRED", "Configure SKU, brand and physical pack first"
                        )
                    raw = ADAPTERS[config["adapter"]](config, transport).fetch(config["url"])
                    response = transport.responses[config["url"]]
                    validate_identity(raw, config)
                    result.update(
                        observed_at=response.observed_at.isoformat(),
                        evidence_sha256=response.sha256,
                    )
                    if not raw.available and raw.price_text is None:
                        result["status"] = "OUT_OF_STOCK"
                    else:
                        quote = normalize(raw, config, response.observed_at, response.sha256)
                        with self.session.begin():
                            self._record_quote(quote, run, config)
                        result["status"] = "OK" if raw.available else "OUT_OF_STOCK"
                except (ScrapeError, RuleViolation) as exc:
                    # Isolate supplier failures and avoid returning raw exceptions or secrets.
                    result.update(status="ERROR", error_code=getattr(exc, "code", "SCRAPE_FAILED"))
                results.append(result)
            completed = sum(result["status"] in {"OK", "OUT_OF_STOCK"} for result in results)
            with self.session.begin():
                run.finished_at = datetime.now(UTC)
                run.status = (
                    "OK" if completed == len(results) else ("PARTIAL" if completed else "ERROR")
                )
                run.message = json.dumps({"products": results}, sort_keys=True)
            runs.append(run)
        # Acquisition happens after the HTTP request starts; freshly fetched quotes are current.
        reference = max([now, *[response.observed_at for response in transport.responses.values()]])
        return {**self.catalog(item_id, reference), "runs": runs}
