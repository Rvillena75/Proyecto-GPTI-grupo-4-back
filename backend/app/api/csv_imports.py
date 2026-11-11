"""Raw CSV body avoids a multipart dependency and caps streamed input before parsing."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.api.auth import Actor
from app.api.routes import Db, Now
from app.services.csv_imports import MAX_BYTES, CsvRejected, error, import_movements

router = APIRouter(tags=["movements"])


class CsvImportResponse(BaseModel):
    imported: int
    movement_ids: list[int]
    replayed: bool


@router.post(
    "/movements/import-csv",
    response_model=CsvImportResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
async def upload_csv(request: Request, db: Db, now: Now, actor: Actor):
    try:
        if request.headers.get("content-type", "").split(";")[0].lower() != "text/csv":
            raise CsvRejected([error(0, "CSV_CONTENT_TYPE", "Use Content-Type text/csv")], 415)
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > MAX_BYTES:
                raise CsvRejected([error(0, "CSV_TOO_LARGE", "Máximo 1 MiB por archivo")], 413)
            content.extend(chunk)
        return await run_in_threadpool(import_movements, db, actor.id, bytes(content), now)
    except CsvRejected as exc:
        return JSONResponse(
            status_code=exc.status,
            content={
                "error": {
                    "code": "CSV_REJECTED",
                    "message": "No se registró ningún movimiento",
                    "details": exc.errors,
                }
            },
        )
