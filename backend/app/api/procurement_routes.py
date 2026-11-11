"""The agreed catalog, persisted prices, order history and receipt routes."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.auth import Actor, Admin, current_user
from app.api.procurement_schemas import (
    OrderReceiptCreate,
    OrderResponse,
    PriceRefreshResponse,
    PricesResponse,
    ScrapeRunResponse,
    SupplierCreate,
    SupplierProductCreate,
    SupplierProductResponse,
    SupplierProductUpdate,
    SupplierResponse,
    SupplierUpdate,
)
from app.api.routes import WRITE_ERRORS, Db, Now
from app.domain.errors import RuleViolation
from app.services.catalog import CatalogService
from app.services.orders import OrderService
from app.services.prices import PriceService
from scraper.runtime import price_transport
from scraper.store import WorkerBusyError
from scraper.transport import PublicTransport

router = APIRouter(responses=WRITE_ERRORS, dependencies=[Depends(current_user)])


def get_price_transport() -> Iterator[PublicTransport]:
    # Share pacing and a process lock with the CLI worker, including repeated API calls.
    try:
        with price_transport() as transport:
            yield transport
    except WorkerBusyError as exc:
        raise RuleViolation(
            "PRICE_WORKER_BUSY", "The public price worker is already running", 409
        ) from exc


Transport = Annotated[PublicTransport, Depends(get_price_transport)]


@router.get("/suppliers", response_model=list[SupplierResponse], tags=["suppliers"])
def list_suppliers(db: Db):
    return CatalogService(db).suppliers()


@router.get("/suppliers/{supplier_id}", response_model=SupplierResponse, tags=["suppliers"])
def get_supplier(supplier_id: int, db: Db):
    return CatalogService(db).supplier(supplier_id)


@router.post("/suppliers", response_model=SupplierResponse, status_code=201, tags=["suppliers"])
def create_supplier(payload: SupplierCreate, db: Db, now: Now, actor: Admin):
    return CatalogService(db).create_supplier(payload.model_dump(mode="python"), now)


@router.patch("/suppliers/{supplier_id}", response_model=SupplierResponse, tags=["suppliers"])
def update_supplier(supplier_id: int, payload: SupplierUpdate, db: Db, now: Now, actor: Admin):
    return CatalogService(db).update_supplier(
        supplier_id, payload.model_dump(exclude_unset=True), now
    )


@router.get(
    "/suppliers/{supplier_id}/products",
    response_model=list[SupplierProductResponse],
    tags=["suppliers"],
)
def list_supplier_products(supplier_id: int, db: Db):
    return CatalogService(db).products(supplier_id)


@router.post(
    "/suppliers/{supplier_id}/products",
    response_model=SupplierProductResponse,
    status_code=201,
    tags=["suppliers"],
)
def create_supplier_product(supplier_id: int, payload: SupplierProductCreate, db: Db, actor: Admin):
    return CatalogService(db).create_product(supplier_id, payload.model_dump(mode="python"))


@router.patch(
    "/suppliers/{supplier_id}/products/{product_id}",
    response_model=SupplierProductResponse,
    tags=["suppliers"],
)
def update_supplier_product(
    supplier_id: int, product_id: int, payload: SupplierProductUpdate, db: Db, actor: Admin
):
    return CatalogService(db).update_product(
        supplier_id, product_id, payload.model_dump(exclude_unset=True)
    )


@router.get(
    "/suppliers/{supplier_id}/scrape-runs", response_model=list[ScrapeRunResponse], tags=["prices"]
)
def supplier_scrape_runs(supplier_id: int, db: Db):
    return CatalogService(db).runs(supplier_id)


@router.get("/prices/{item_id}", response_model=PricesResponse, tags=["prices"])
def get_prices(item_id: int, db: Db, now: Now):
    return PriceService(db).catalog(item_id, now)


@router.post("/prices/{item_id}/refresh", response_model=PriceRefreshResponse, tags=["prices"])
def refresh_prices(item_id: int, db: Db, now: Now, actor: Admin, transport: Transport):
    return PriceService(db).refresh(item_id, now, transport)


@router.get("/orders", response_model=list[OrderResponse], tags=["orders"])
def list_orders(db: Db):
    """Persisted history, with no invented filters or pagination."""
    return OrderService(db).orders()


@router.post("/orders/{order_id}/receipt", response_model=OrderResponse, tags=["orders"])
def receive_order(
    order_id: int, db: Db, now: Now, actor: Actor, payload: OrderReceiptCreate | None = None
):
    """Full receipt; an empty body suffices for fixed-content approved packs."""
    data = payload or OrderReceiptCreate()
    return OrderService(db, actor.id).receive(
        order_id, now, data.received_at, data.received_quantity
    )
