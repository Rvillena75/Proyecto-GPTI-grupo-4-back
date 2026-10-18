"""ADMIN period indicators with transparent event definitions and missing-data states."""

from datetime import datetime

from fastapi import APIRouter

from app.api.auth import Admin
from app.api.routes import Db
from app.services.metrics import metrics

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def get_metrics(start: datetime, end: datetime, db: Db, actor: Admin):
    return metrics(db, start, end)
