"""REQ-18/RN-19: bounded CSV parsing and atomic, replay-safe inventory imports."""

import csv
import hashlib
import io
import json
import re
from datetime import UTC, datetime, time
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.domain.errors import RuleViolation
from app.persistence.models import CsvImport, Item
from app.services.inventory import InventoryService

MAX_BYTES = 1_048_576
MAX_ROWS = 1000
HEADERS = ["insumo", "cantidad", "unidad", "tipo", "fecha", "observacion"]


class CsvRejected(Exception):
    def __init__(self, errors: list[dict], status: int = 422):
        self.errors = errors
        self.status = status


def error(row: int, code: str, message: str) -> dict:
    return {"row": row, "code": code, "message": message}


def parse_csv(content: bytes) -> tuple[list[dict], list[dict]]:
    if len(content) > MAX_BYTES:
        raise CsvRejected([error(0, "CSV_TOO_LARGE", "Máximo 1 MiB por archivo")], 413)
    try:
        decoded = content.decode("utf-8-sig")
        if "\x00" in decoded:
            raise ValueError
        first = decoded.splitlines()[0] if decoded.splitlines() else ""
        delimiter = ";" if first.count(";") > first.count(",") else ","
        reader = csv.reader(io.StringIO(decoded, newline=""), delimiter=delimiter, strict=True)
        if next(reader, None) != HEADERS:
            raise CsvRejected([error(0, "CSV_HEADER", "Encabezado: " + delimiter.join(HEADERS))])
        rows, errors = [], []
        seen = set()
        for index, fields in enumerate(reader, 1):
            if index > MAX_ROWS:
                raise CsvRejected([error(0, "CSV_TOO_MANY_ROWS", "Máximo 1000 filas de datos")])
            try:
                if len(fields) != len(HEADERS):
                    raise ValueError("Se requieren seis columnas, incluso observacion vacía")
                item, quantity, unit, kind, date, note = (v.strip() for v in fields)
                if not re.fullmatch(r"[1-9][0-9]{0,17}", item):
                    raise ValueError("insumo debe ser un ID entero positivo")
                if not re.fullmatch(r"[0-9]+(?:[.,][0-9]+)?", quantity):
                    raise ValueError("cantidad debe ser un número positivo sin separador de miles")
                qty = Decimal(quantity.replace(",", "."))
                if qty <= 0:
                    raise ValueError("cantidad debe ser mayor a cero")
                if kind not in {"ENTRADA", "SALIDA"}:
                    raise ValueError("tipo debe ser ENTRADA o SALIDA")
                if unit not in {"kg", "g", "L", "mL", "unidad"}:
                    raise ValueError("unidad debe ser kg, g, L, mL o unidad")
                if len(note) > 2000:
                    raise ValueError("observacion admite hasta 2000 caracteres")
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                    occurred = datetime.combine(
                        datetime.fromisoformat(date).date(), time(), ZoneInfo("America/Santiago")
                    )
                else:
                    occurred = datetime.fromisoformat(date.replace("Z", "+00:00"))
                    if occurred.tzinfo is None:
                        raise ValueError("fecha necesita zona horaria o formato AAAA-MM-DD")
                row = {
                    "item_id": int(item),
                    "quantity": str(qty.normalize()),
                    "unit": unit,
                    "kind": "ENTRY" if kind == "ENTRADA" else "EXIT",
                    "occurred_at": occurred.astimezone(UTC).isoformat(),
                    "note": note or None,
                }
                signature = json.dumps(row, sort_keys=True, ensure_ascii=False)
                if signature in seen:
                    raise ValueError("Fila duplicada dentro del archivo")
                seen.add(signature)
                rows.append({"row": index, **row})
            except (ValueError, InvalidOperation, OverflowError) as exc:
                errors.append(error(index, "CSV_ROW", str(exc)))
        if not rows and not errors:
            errors.append(error(0, "CSV_EMPTY", "El archivo no contiene movimientos"))
        return rows, errors
    except CsvRejected:
        raise
    except (UnicodeError, csv.Error, ValueError) as exc:
        raise CsvRejected([error(0, "CSV_INVALID", "Archivo CSV UTF-8 malformado")]) from exc


def import_movements(session: Session, actor_id: int, content: bytes, now: datetime) -> dict:
    rows, errors = parse_csv(content)
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    with session.begin():
        # Serializes retries, including concurrent uploads, without persisting the source file.
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int(digest[:15], 16)})
        previous = session.get(CsvImport, digest) if not errors else None
        if previous is not None:
            return {
                "imported": len(previous.movement_ids),
                "movement_ids": previous.movement_ids,
                "replayed": True,
            }
        # Acquire all item locks in a stable order before applying rows in file order.
        session.scalars(
            select(Item)
            .where(Item.id.in_({r["item_id"] for r in rows}))
            .order_by(Item.id)
            .with_for_update()
        ).all()
        service = InventoryService(session, actor_id)
        ids = []
        for row in rows:
            try:
                with session.begin_nested():
                    movement = service.add_movement(
                        row["item_id"],
                        row["kind"],
                        Decimal(row["quantity"]),
                        row["unit"],
                        now,
                        datetime.fromisoformat(row["occurred_at"]),
                        row["note"],
                        _join_transaction=True,
                    )
                    ids.append(movement.id)
            except RuleViolation as exc:
                errors.append(error(row["row"], exc.code, exc.message))
        if errors:
            raise CsvRejected(sorted(errors, key=lambda e: e["row"]))
        session.add(CsvImport(digest=digest, movement_ids=ids, created_by=actor_id, created_at=now))
    return {"imported": len(ids), "movement_ids": ids, "replayed": False}
