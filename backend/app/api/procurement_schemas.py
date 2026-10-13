"""REST contracts for the agreed supplier, price and order endpoints."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from app.api.schemas import InputModel
from app.e2.contracts import PackSpec


class ScrapeConfig(InputModel):
    sku: str = Field(min_length=1, max_length=100)
    brand: str = Field(min_length=1, max_length=200)
    expected_name_tokens: list[str] = Field(min_length=1)
    pack: PackSpec

    @field_validator("expected_name_tokens")
    @classmethod
    def nonblank_tokens(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("Expected product tokens cannot be blank")
        return values


class SupplierCreate(InputModel):
    name: str = Field(min_length=1, max_length=200)
    price_source_url: str | None = Field(default=None, max_length=1000)
    lead_time_days: StrictInt = Field(ge=0, le=2_147_483_647)
    adapter: Literal["central", "jumbo", "santa_isabel"] | None = None
    is_active: StrictBool = True


class SupplierUpdate(InputModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    price_source_url: str | None = Field(default=None, max_length=1000)
    lead_time_days: StrictInt | None = Field(default=None, ge=0, le=2_147_483_647)
    adapter: Literal["central", "jumbo", "santa_isabel"] | None = None
    is_active: StrictBool | None = None


class SupplierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    price_source_url: str | None
    lead_time_days: int
    adapter: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SupplierProductCreate(InputModel):
    item_id: StrictInt = Field(gt=0)
    name_on_site: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=1000)
    pack_quantity: Decimal = Field(gt=0)
    min_packs: StrictInt = Field(default=1, ge=1, le=2_147_483_647)
    is_active: StrictBool = True
    scrape_config: ScrapeConfig | None = None


class SupplierProductUpdate(InputModel):
    name_on_site: str | None = Field(default=None, min_length=1, max_length=300)
    url: str | None = Field(default=None, min_length=1, max_length=1000)
    pack_quantity: Decimal | None = Field(default=None, gt=0)
    min_packs: StrictInt | None = Field(default=None, ge=1, le=2_147_483_647)
    is_active: StrictBool | None = None
    scrape_config: ScrapeConfig | None = None


class SupplierProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    supplier_id: int
    item_id: int
    name_on_site: str
    url: str
    pack_quantity: Decimal
    min_packs: int
    is_active: bool
    scrape_config: ScrapeConfig | None


class ScrapeRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    supplier_id: int
    started_at: datetime
    finished_at: datetime | None
    status: Literal["RUNNING", "OK", "PARTIAL", "ERROR"]
    message: str | None


class PriceOfferResponse(BaseModel):
    supplier_id: int
    supplier: str
    supplier_product_id: int
    quote_id: int | None
    pack_quantity: Decimal
    pack_price_clp: Decimal | None
    unit_price_clp: Decimal | None
    available: bool | None
    observed_at: datetime | None
    valid_until: datetime | None
    state: str


class PricesResponse(BaseModel):
    item_id: int
    required_quantity: Decimal | None
    offers: list[PriceOfferResponse]
    comparison: dict


class PriceRefreshResponse(PricesResponse):
    runs: list[ScrapeRunResponse]


class OrderResponse(BaseModel):
    id: int
    suggestion_id: int
    code: str
    status: Literal["APPROVED", "RECEIVED"]
    item_id: int
    supplier_product_id: int | None
    packs: int | None
    purchased_qty: Decimal | None
    total_clp: Decimal | None
    decided_by: int | None
    decided_at: datetime
    expected_at: datetime | None
    received_at: datetime | None
    receipt_movement_id: int | None
    received_quantity: Decimal | None


class OrderReceiptCreate(InputModel):
    received_at: datetime | None = None
    received_quantity: Decimal | None = Field(
        default=None,
        gt=0,
        description="Measured base-unit weight for variable-weight packs; full receipt only.",
    )
