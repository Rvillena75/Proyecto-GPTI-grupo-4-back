"""FastAPI entry point for the StockSmart inventory slice."""

from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.routes import router
from app.config import cors_origins
from app.domain.errors import RuleViolation
from app.persistence.database import get_session

app = FastAPI(
    title="StockSmart inventory API",
    version="0.1.0",
    description=(
        "Backend-only P1–P5 API. Prices, suppliers, orders and pilot metrics "
        "are outside this slice."
    ),
)
app.include_router(router)


def add_cors(application: FastAPI, origins: list[str]) -> None:
    """Lets the configured frontend origins call the API from the browser.

    Authentication travels as a Bearer header, not cookies, so credentials stay disabled.
    """
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )


add_cors(app, cors_origins())


@app.exception_handler(RuleViolation)
async def rule_violation_handler(request: Request, exc: RuleViolation) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}}
    )


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "INVALID_REQUEST",
                "message": "Request validation failed",
                "details": [
                    {"loc": error["loc"], "message": error["msg"], "type": error["type"]}
                    for error in exc.errors()
                ],
            }
        },
    )


@app.get(
    "/health",
    tags=["system"],
    response_model=None,
    responses={
        503: {
            "description": "PostgreSQL unavailable",
            "content": {"application/json": {"example": {"status": "unavailable"}}},
        }
    },
)
def health(db: Annotated[Session, Depends(get_session)]) -> dict | JSONResponse:
    """Reports healthy only when PostgreSQL is reachable."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ok"}
