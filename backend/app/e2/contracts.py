"""Minimum exchange contract for Pedro's future order model, without new DB tables."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from app.domain.commercial import CommercialPack

OrderId = Annotated[StrictInt, Field(gt=0)] | Annotated[str, Field(min_length=1, max_length=100)]


class PackSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    commercial_format: str = Field(min_length=1)
    content_quantity: Decimal = Field(gt=0)
    content_unit: str
    units_per_pack: StrictInt = Field(gt=0)
    base_unit: Literal["kg", "L", "unidad"]
    is_variable_weight: bool = False

    def to_pack(self) -> CommercialPack:
        return CommercialPack(
            self.commercial_format,
            self.content_quantity,
            self.content_unit,
            self.units_per_pack,
            self.base_unit,
        )

    @model_validator(mode="after")
    def compatible_units(self) -> "PackSpec":
        self.to_pack()
        if self.is_variable_weight and self.base_unit != "kg":
            raise ValueError("Variable weighed products require a mass base unit")
        return self


class ApprovedOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: OrderId
    code: str = Field(min_length=1, max_length=100)
    status: Literal["APPROVED", "RECEIVED", "REJECTED"]
    supplier_id: StrictInt = Field(gt=0)
    supplier_product_id: StrictInt = Field(gt=0)
    item_id: StrictInt = Field(gt=0)
    packs: StrictInt = Field(gt=0)
    pack: PackSpec
    issued_at: datetime
    approved_at: datetime
    expected_at: datetime
    lead_time_days: StrictInt = Field(ge=0)

    @field_validator("issued_at", "approved_at", "expected_at")
    @classmethod
    def aware_dates(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timezone-aware timestamp required")
        return value

    @model_validator(mode="after")
    def ordered_dates(self) -> "ApprovedOrder":
        if self.approved_at < self.issued_at or self.expected_at < self.approved_at:
            raise ValueError("Issue, approval and expected receipt must be chronological")
        return self
