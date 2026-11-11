"""FastAPI entry point for the StockSmart inventory slice."""

import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from app.api.auth import router as auth_router
from app.api.metrics import router as metrics_router
from app.api.procurement_routes import router as procurement_router
from app.api.routes import router
from app.api.schemas import ErrorResponse, HealthResponse
from app.api.suggestions import router as suggestion_router
from app.config import connection_timeout, cors_origins, database_url
from app.domain.errors import RuleViolation
from app.persistence.database import get_session
from app.services.auth import signing_key, token_minutes

LOG = logging.getLogger("stocksmart.api")
SCHEMA_REVISION = "0005_e2_completion"


@asynccontextmanager
async def lifespan(application: FastAPI):
    signing_key()
    token_minutes()
    database_url()
    connection_timeout()
    yield


app = FastAPI(
    title="StockSmart inventory API",
    version="0.2.0",
    lifespan=lifespan,
    responses={
        code: {"model": ErrorResponse}
        for code in (400, 401, 403, 404, 405, 409, 422, 429, 500, 503)
    },
    description=(
        "Inventory, suppliers, public scraped prices and order receipt API. "
        "JWT roles, persisted suggestions and human decisions. No automatic purchases."
    ),
)
app.include_router(router)
app.include_router(procurement_router)
app.include_router(auth_router)
app.include_router(suggestion_router)
app.include_router(metrics_router)


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


@app.middleware("http")
async def correlation(request: Request, call_next):
    request.state.request_id = uuid4().hex
    try:
        response = await call_next(request)
    except Exception as exc:
        response = await unexpected_error(request, exc)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={
            "error": {"code": f"HTTP_{exc.status_code}", "message": "Solicitud HTTP no válida"}
        },
    )


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    LOG.error(
        "database_unavailable request_id=%s type=%s",
        getattr(request.state, "request_id", "unknown"),
        type(exc).__name__,
    )
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "DATABASE_UNAVAILABLE",
                "message": "Persistencia temporalmente no disponible",
            }
        },
    )


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", uuid4().hex)
    # Log type and correlation only. SQL/URLs/passwords in exception text are not public logs.
    LOG.error("unhandled request_id=%s type=%s", request_id, type(exc).__name__)
    return JSONResponse(
        status_code=500,
        headers={"X-Request-ID": request_id},
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Error interno; referencia " + request_id,
            }
        },
    )


@app.exception_handler(RuleViolation)
async def rule_violation_handler(request: Request, exc: RuleViolation) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else {}
    return JSONResponse(
        status_code=exc.status,
        headers=headers,
        content={"error": {"code": exc.code, "message": exc.message}},
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
    "/ready",
    tags=["system"],
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "Database/schema unavailable"}},
)
@app.get(
    "/health",
    tags=["system"],
    response_model=HealthResponse,
    responses={
        503: {
            "model": HealthResponse,
            "description": "PostgreSQL unavailable",
            "content": {"application/json": {"example": {"status": "unavailable"}}},
        }
    },
)
def health(db: Annotated[Session, Depends(get_session)]) -> dict | JSONResponse:
    """Bounded database and schema readiness, suitable for Render health checks."""
    try:
        revision = db.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != SCHEMA_REVISION:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        db.execute(text("SELECT id, password_hash, role FROM users LIMIT 0"))
        db.execute(text("SELECT decided_at, inputs_snapshot FROM suggestions LIMIT 0"))
        db.execute(text("SELECT evidence_snapshot FROM price_quotes LIMIT 0"))
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ok"}


@app.get("/live", tags=["system"])
def live() -> dict:
    return {"status": "alive"}


@app.get("/version", tags=["system"])
def version() -> dict:
    commit = os.getenv("RENDER_GIT_COMMIT") or os.getenv("STOCKSMART_BUILD_SHA")
    if not commit or not re.fullmatch("[0-9a-fA-F]{40}", commit):
        commit = None
    return {"version": app.version, "commit": commit, "expected_schema_revision": SCHEMA_REVISION}


# Outermost user middleware also decorates sanitized unexpected error responses with CORS.
add_cors(app, cors_origins())
