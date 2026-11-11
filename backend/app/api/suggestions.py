"""Backend purchase workflow; no external order or automatic purchase is emitted."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import Actor, Admin, current_user
from app.api.routes import Db, Now
from app.api.schemas import InputModel
from app.services.suggestions import SuggestionService

router = APIRouter(tags=["suggestions"], dependencies=[Depends(current_user)])


class SuggestionCreate(InputModel):
    item_id: int = Field(gt=0)


class DecisionCreate(InputModel):
    decision: Literal["APPROVE", "REJECT"]
    reason: str | None = Field(default=None, max_length=2000)


class SuggestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    item_id: int
    status: Literal["PENDING", "APPROVED", "REJECTED"]
    required_qty: Decimal
    in_transit_qty: Decimal
    supplier_product_id: int | None
    packs: int | None
    purchased_qty: Decimal | None
    total_clp: Decimal | None
    next_best_total_clp: Decimal | None
    savings_clp: Decimal | None
    inputs_snapshot: dict
    created_at: datetime
    created_by: int | None
    decided_by: int | None
    decided_at: datetime | None
    rejection_reason: str | None


@router.post("/suggestions", response_model=SuggestionResponse, status_code=201)
def generate(payload: SuggestionCreate, db: Db, now: Now, actor: Admin):
    return SuggestionService(db, actor.id).generate(payload.item_id, now)


@router.get("/suggestions", response_model=list[SuggestionResponse])
def list_suggestions(db: Db, actor: Actor, item_id: int | None = None):
    return SuggestionService(db, actor.id).list(item_id)


@router.get("/suggestions/{suggestion_id}", response_model=SuggestionResponse)
def get_suggestion(suggestion_id: int, db: Db, actor: Actor):
    return SuggestionService(db, actor.id).get(suggestion_id)


@router.post("/suggestions/{suggestion_id}/decision", response_model=SuggestionResponse)
def decide(suggestion_id: int, payload: DecisionCreate, db: Db, now: Now, actor: Admin):
    return SuggestionService(db, actor.id).decide(
        suggestion_id, payload.decision, payload.reason, now
    )
