"""JSON contract for the API (frozen v1.2 for the inventory slice)."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemCreate(InputModel):
    name: str = Field(min_length=1, max_length=200, examples=["Azúcar blanca"])
    base_unit: Literal["kg", "L", "unidad"]
    initial_quantity: Decimal = Field(ge=0, examples=["25"])
    initial_unit: str = Field(examples=["kg"])
    timezone: str = Field(default="America/Santiago", examples=["America/Santiago"])
    manual_safe_stock: Decimal | None = Field(default=None, ge=0)


class ItemUpdate(InputModel):
    """Partial update. Only the fields sent are changed; the base unit cannot change."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    manual_safe_stock: Decimal | None = Field(
        default=None,
        ge=0,
        description="Send null to restore the automatic threshold; omit to leave it unchanged.",
    )


class InitialStockCreate(InputModel):
    quantity: Decimal = Field(ge=0)
    unit: str


class MovementCreate(InputModel):
    item_id: int
    kind: Literal["ENTRY", "EXIT"]
    quantity: Decimal = Field(gt=0, examples=["3"])
    unit: str = Field(examples=["kg"])
    occurred_at: datetime | None = Field(
        default=None,
        description="Actual occurrence time; omitted means now. Timezone-aware.",
    )
    note: str | None = None


class ReverseCreate(InputModel):
    occurred_at: datetime | None = None


class CorrectionCreate(InputModel):
    note: str = Field(min_length=1, examples=["Salida duplicada; conteo posterior ya la absorbió"])
    stock_effect: Decimal = Field(
        default=Decimal(0),
        description=(
            "Signed effect in the item's base unit, applied now. Zero documents an error "
            "already absorbed by a count; a nonzero value corrects only current stock."
        ),
        examples=["0", "2", "-1"],
    )


class CountCreate(InputModel):
    item_id: int
    physical_quantity: Decimal = Field(ge=0, examples=["22"])
    unit: str = Field(examples=["kg"])
    cause: (
        Literal["vencimiento", "deterioro", "error de registro", "desconocida", "otra"] | None
    ) = None
    corrects_movement_id: int | None = None


class SafeStockUpdate(InputModel):
    manual_safe_stock: Decimal | None = Field(
        description="Set a nonnegative manual threshold; null restores the automatic threshold."
    )


class ConsumptionResponse(BaseModel):
    status: Literal["INSUFFICIENT_HISTORY", "PRELIMINARY", "ESTABLISHED"]
    complete_days: int
    observed_quantity: Decimal
    weekly_average: Decimal | None
    window_start: datetime | None
    window_end: datetime | None


class AssessmentResponse(BaseModel):
    safe_stock: Decimal | None
    alert_source: Literal["MANUAL_THRESHOLD", "AUTOMATIC"] | None
    alert_status: Literal["LOW_STOCK", "IN_TRANSIT", "OK", "INSUFFICIENT_HISTORY"] = Field(
        description="IN_TRANSIT: low stock already attended by an approved order not yet received."
    )
    is_low_stock: bool | None
    target_stock: Decimal | None
    suggested_quantity: Decimal | None
    daily_average: Decimal | None
    remaining_days: Decimal | None
    depletion_at: datetime | None
    depletion_status: Literal[
        "INSUFFICIENT_HISTORY", "ZERO_CONSUMPTION", "PROJECTED", "OUT_OF_RANGE"
    ]
    lead_time_days: int | None = Field(
        description="Longest delivery time (calendar days) among active suppliers; null if none."
    )
    projected_stock_at_arrival: Decimal | None = Field(
        description="Stock left when an order placed now would arrive (never below zero)."
    )
    in_transit_quantity: Decimal = Field(
        description="Base-unit quantity in approved orders not yet received."
    )
    order_by_at: datetime | None = Field(
        description="Latest moment to order so it arrives before falling to the safe stock. "
        "A past value means the order is overdue."
    )


class ItemStateResponse(BaseModel):
    id: int
    name: str
    base_unit: Literal["kg", "L", "unidad"]
    timezone: str
    current_stock: Decimal
    manual_safe_stock: Decimal | None
    observation_started_at: datetime | None
    created_at: datetime
    updated_at: datetime
    consumption: ConsumptionResponse
    assessment: AssessmentResponse


class UserRef(BaseModel):
    id: int
    name: str


class MovementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: int
    kind: str
    delta: Decimal
    input_quantity: Decimal | None
    input_unit: str | None
    occurred_at: datetime
    created_at: datetime
    invalidates_movement_id: int | None
    note: str | None
    balance_after: Decimal = Field(
        description="Item stock right after this movement, in order of occurrence "
        "(occurred_at, created_at, id)."
    )
    created_by: UserRef | None = Field(description="Null until authentication exists.")


class CountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: int
    adjustment_movement_id: int
    corrects_movement_id: int | None
    theoretical_before: Decimal
    physical_stock: Decimal
    difference: Decimal
    shortage: Decimal
    cause: str | None
    occurred_at: datetime
    created_at: datetime


class ShortageRateResponse(BaseModel):
    status: Literal["CALCULABLE", "NOT_CALCULABLE"]
    available: Decimal | None
    shortage: Decimal
    rate_percent: Decimal | None


class AlertResponse(BaseModel):
    item_id: int
    current_stock: Decimal
    safe_stock: Decimal | None
    alert_source: str | None
    alert_status: str
    is_low_stock: bool | None
    lead_time_days: int | None
    projected_stock_at_arrival: Decimal | None
    in_transit_quantity: Decimal
    order_by_at: datetime | None


class ValidationIssue(BaseModel):
    loc: list[str | int]
    message: str
    type: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[ValidationIssue] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
