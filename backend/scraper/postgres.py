"""Daily/on-demand public price worker using the same PostgreSQL boundary as the API."""

import argparse
import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.api.procurement_schemas import PriceRefreshResponse
from app.persistence.database import session_factory
from app.persistence.models import Supplier, SupplierProduct
from app.services.prices import PriceService
from scraper.runtime import price_transport
from scraper.store import WorkerBusyError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--item-id", type=int)
    group.add_argument("--all-active", action="store_true")
    args = parser.parse_args(argv)
    if args.item_id is not None and args.item_id <= 0:
        parser.error("--item-id must be positive")
    statuses = []
    try:
        with price_transport() as transport:
            with session_factory()() as session:
                items = (
                    [args.item_id]
                    if args.item_id
                    else list(
                        session.scalars(
                            select(SupplierProduct.item_id)
                            .join(Supplier)
                            .where(SupplierProduct.is_active, Supplier.is_active)
                            .distinct()
                            .order_by(SupplierProduct.item_id)
                        )
                    )
                )
                session.rollback()  # No read transaction leaks into refresh().
                for item_id in items:
                    result = PriceService(session).refresh(item_id, datetime.now(UTC), transport)
                    output = PriceRefreshResponse.model_validate(result).model_dump(mode="json")
                    print(json.dumps(output, ensure_ascii=False))
                    statuses.extend(run.status for run in result["runs"])
                    session.rollback()
    except WorkerBusyError:
        print(json.dumps({"status": "BUSY", "error_code": "PRICE_WORKER_BUSY"}))
        return 3
    except Exception as exc:
        code = "DATABASE_UNAVAILABLE" if isinstance(exc, SQLAlchemyError) else "WORKER_FAILED"
        print(json.dumps({"status": "ERROR", "error_code": code, "type": type(exc).__name__}))
        return 1
    if statuses and all(status == "OK" for status in statuses):
        return 0
    return 2 if any(status in {"OK", "PARTIAL"} for status in statuses) else 1


if __name__ == "__main__":
    raise SystemExit(main())
