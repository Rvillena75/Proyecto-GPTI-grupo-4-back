"""Thin HTTP mapping for inventory use cases."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.schemas import (
    AlertResponse,
    CorrectionCreate,
    CountCreate,
    CountResponse,
    ErrorResponse,
    InitialStockCreate,
    ItemCreate,
    ItemStateResponse,
    MovementCreate,
    MovementResponse,
    ReverseCreate,
    SafeStockUpdate,
    ShortageRateResponse,
)
from app.persistence.database import get_session
from app.services.inventory import InventoryService

router = APIRouter()
VALIDATION_ERROR = {422: {"model": ErrorResponse, "description": "Invalid request"}}
ITEM_ERRORS = {404: {"model": ErrorResponse, "description": "Item not found"}, **VALIDATION_ERROR}
WRITE_ERRORS = {
    404: {"model": ErrorResponse, "description": "Item or movement not found"},
    409: {"model": ErrorResponse, "description": "Business rule conflict"},
    **VALIDATION_ERROR,
}


def get_now() -> datetime:
    return datetime.now(UTC)


Db = Annotated[Session, Depends(get_session)]
Now = Annotated[datetime, Depends(get_now)]


def _alert(state: dict) -> AlertResponse:
    assessment = state["assessment"]
    return AlertResponse(
        item_id=state["id"],
        current_stock=state["current_stock"],
        safe_stock=assessment.safe_stock,
        alert_source=assessment.alert_source,
        alert_status=assessment.alert_status,
        is_low_stock=assessment.is_low_stock,
    )


@router.post(
    "/items",
    response_model=ItemStateResponse,
    status_code=201,
    responses=VALIDATION_ERROR,
    tags=["items"],
)
def create_item(payload: ItemCreate, db: Db, now: Now) -> dict:
    """Create an item and its non-backdated initial stock in one transaction."""
    item = InventoryService(db).create_item(
        payload.name,
        payload.base_unit,
        payload.initial_quantity,
        payload.initial_unit,
        payload.timezone,
        payload.manual_safe_stock,
        now,
    )
    return InventoryService(db).status(item.id, now)


@router.get(
    "/items/{item_id}", response_model=ItemStateResponse, responses=ITEM_ERRORS, tags=["items"]
)
def get_item(item_id: int, db: Db, now: Now) -> dict:
    """Stock, consumption, forecast, alert and base-unit replenishment need."""
    return InventoryService(db).status(item_id, now)


@router.post(
    "/items/{item_id}/initial-stock",
    response_model=MovementResponse,
    status_code=201,
    responses=WRITE_ERRORS,
    tags=["items"],
)
def replace_initial(item_id: int, payload: InitialStockCreate, db: Db, now: Now):  # type: ignore[no-untyped-def]
    """Start a new observation after the previous initial stock was validly reversed."""
    return InventoryService(db).replace_initial(item_id, payload.quantity, payload.unit, now)


@router.put(
    "/items/{item_id}/safe-stock",
    response_model=ItemStateResponse,
    responses=ITEM_ERRORS,
    tags=["items"],
)
def update_safe_stock(item_id: int, payload: SafeStockUpdate, db: Db, now: Now) -> dict:
    """A null value removes the manual override and restores dynamic P5 behavior."""
    service = InventoryService(db)
    service.set_manual_safe_stock(item_id, payload.manual_safe_stock, now)
    return service.status(item_id, now)


@router.post(
    "/movements",
    response_model=MovementResponse,
    status_code=201,
    responses=WRITE_ERRORS,
    tags=["movements"],
)
def create_movement(payload: MovementCreate, db: Db, now: Now):  # type: ignore[no-untyped-def]
    """Post an entry or exit; backdated events are checked against the full ledger and counts."""
    return InventoryService(db).add_movement(
        payload.item_id,
        payload.kind,
        payload.quantity,
        payload.unit,
        now,
        payload.occurred_at,
        payload.note,
    )


@router.post(
    "/movements/{movement_id}/reverse",
    response_model=MovementResponse,
    status_code=201,
    responses=WRITE_ERRORS,
    tags=["movements"],
)
def reverse_movement(movement_id: int, payload: ReverseCreate, db: Db, now: Now):  # type: ignore[no-untyped-def]
    """Exact inverse only when the effective history remains valid (D1/D6)."""
    return InventoryService(db).reverse(movement_id, now, payload.occurred_at)


@router.post(
    "/movements/{movement_id}/corrections",
    response_model=MovementResponse,
    status_code=201,
    responses=WRITE_ERRORS,
    tags=["movements"],
)
def create_correction(movement_id: int, payload: CorrectionCreate, db: Db, now: Now):  # type: ignore[no-untyped-def]
    """Record a present D6 correction with its signed base-unit stock effect."""
    return InventoryService(db).record_correction(
        movement_id, payload.note, now, payload.stock_effect
    )


@router.post(
    "/counts",
    response_model=CountResponse,
    status_code=201,
    responses=WRITE_ERRORS,
    tags=["counts"],
)
def create_count(payload: CountCreate, db: Db, now: Now):  # type: ignore[no-untyped-def]
    """Atomically reconcile theoretical and physical stock; optionally link a correction."""
    return InventoryService(db).record_count(
        payload.item_id,
        payload.physical_quantity,
        payload.unit,
        now,
        payload.cause,
        payload.corrects_movement_id,
    )


@router.get(
    "/items/{item_id}/movements",
    response_model=list[MovementResponse],
    responses=ITEM_ERRORS,
    tags=["movements"],
)
def list_movements(item_id: int, db: Db):
    """Read-only movement audit trail; there is no edit or delete endpoint."""
    return InventoryService(db).movements(item_id)


@router.get(
    "/items/{item_id}/counts",
    response_model=list[CountResponse],
    responses=ITEM_ERRORS,
    tags=["counts"],
)
def list_counts(item_id: int, db: Db):
    """Read-only confirmed physical counts."""
    return InventoryService(db).counts(item_id)


@router.get(
    "/items/{item_id}/consumption",
    response_model=ItemStateResponse,
    responses=ITEM_ERRORS,
    tags=["forecast"],
)
def consumption_and_forecast(item_id: int, db: Db, now: Now) -> dict:
    """Return the same consistent snapshot as GET /items/{id}."""
    return InventoryService(db).status(item_id, now)


@router.get(
    "/items/{item_id}/alert", response_model=AlertResponse, responses=ITEM_ERRORS, tags=["alerts"]
)
def item_alert(item_id: int, db: Db, now: Now) -> AlertResponse:
    """Current P5 alert; may be INSUFFICIENT_HISTORY."""
    return _alert(InventoryService(db).status(item_id, now))


@router.get("/alerts", response_model=list[AlertResponse], tags=["alerts"])
def list_alerts(db: Db, now: Now) -> list[AlertResponse]:
    """Currently active low-stock alerts. Use the item alert route for other states."""
    service = InventoryService(db)
    alerts = [_alert(service.status(item.id, now)) for item in service.items()]
    return [alert for alert in alerts if alert.is_low_stock]


@router.get(
    "/items/{item_id}/shortage-rate",
    response_model=ShortageRateResponse,
    responses=ITEM_ERRORS,
    tags=["counts"],
)
def item_shortage_rate(
    item_id: int,
    db: Db,
    start: Annotated[datetime, Query(description="Inclusive, timezone-aware")],
    end: Annotated[datetime, Query(description="Exclusive, timezone-aware")],
) -> dict:
    """P3 rate for a caller-selected comparable period, without pilot metrics."""
    return InventoryService(db).shortage_rate(item_id, start, end)
