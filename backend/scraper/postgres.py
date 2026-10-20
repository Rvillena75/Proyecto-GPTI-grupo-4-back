"""Daily/on-demand public price worker using the same PostgreSQL boundary as the API."""

import argparse
import json
import logging
import os
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.procurement_schemas import PriceRefreshResponse
from app.persistence.database import session_factory
from app.persistence.models import ScrapeRun, Supplier, SupplierProduct
from app.services.prices import PriceService
from scraper.runtime import price_transport
from scraper.store import WorkerBusyError

LOG = logging.getLogger("stocksmart.prices")
DEFAULT_TIMEZONE = "America/Santiago"


def resolve_timezone(timezone_name: str = DEFAULT_TIMEZONE) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unsupported timezone: {timezone_name}") from exc


def active_supplier_ids(session: Session, *, item_id: int | None = None) -> list[int]:
    query = (
        select(SupplierProduct.supplier_id)
        .join(Supplier, Supplier.id == SupplierProduct.supplier_id)
        .where(SupplierProduct.is_active, Supplier.is_active)
    )
    if item_id is not None:
        query = query.where(SupplierProduct.item_id == item_id)
    return list(session.scalars(query.distinct().order_by(SupplierProduct.supplier_id)))


def has_unscraped_suppliers(session: Session, *, item_id: int | None = None) -> bool:
    """Return True when at least one active supplier with active products has no finished runs."""
    suppliers = active_supplier_ids(session, item_id=item_id)
    if not suppliers:
        return False
    with_runs = set(
        session.scalars(
            select(ScrapeRun.supplier_id)
            .where(ScrapeRun.supplier_id.in_(suppliers), ScrapeRun.finished_at.is_not(None))
            .distinct()
        )
    )
    return any(supplier_id not in with_runs for supplier_id in suppliers)


def due_supplier_ids(
    session: Session,
    now: datetime,
    *,
    timezone_name: str = DEFAULT_TIMEZONE,
    item_id: int | None = None,
) -> list[int]:
    """Return active suppliers that have not yet completed an OK scrape on today's local date."""
    zone = resolve_timezone(timezone_name)
    suppliers = active_supplier_ids(session, item_id=item_id)
    if not suppliers:
        return []
    target_date = now.astimezone(zone).date()
    runs = session.scalars(
        select(ScrapeRun).where(
            ScrapeRun.supplier_id.in_(suppliers),
            ScrapeRun.status == "OK",
            ScrapeRun.finished_at.is_not(None),
        )
    ).all()
    completed_today = {
        run.supplier_id
        for run in runs
        if run.finished_at is not None and run.finished_at.astimezone(zone).date() == target_date
    }
    return [supplier_id for supplier_id in suppliers if supplier_id not in completed_today]


def is_daily_scrape_due(
    session: Session,
    now: datetime,
    *,
    timezone_name: str = DEFAULT_TIMEZONE,
    item_id: int | None = None,
) -> bool:
    return bool(due_supplier_ids(session, now, timezone_name=timezone_name, item_id=item_id))


def run_worker(
    *,
    item_id: int | None = None,
    only_if_due: bool = False,
    timezone_name: str = DEFAULT_TIMEZONE,
    now: datetime | None = None,
    emit_stdout: bool = True,
) -> tuple[int, dict]:
    """Execute the price worker once, coordinating via PostgreSQL advisory lock."""
    zone = resolve_timezone(timezone_name)
    current_time = now or datetime.now(UTC)
    statuses: list[str] = []
    outputs: list[dict] = []
    try:
        if only_if_due:
            with session_factory()() as session:
                due = due_supplier_ids(
                    session, current_time, timezone_name=timezone_name, item_id=item_id
                )
                session.rollback()
            if not due:
                payload = {
                    "status": "SKIPPED",
                    "reason": "ALREADY_COMPLETED_TODAY",
                    "timezone": timezone_name,
                    "local_date": current_time.astimezone(zone).date().isoformat(),
                }
                LOG.info(
                    "price_worker_skipped reason=ALREADY_COMPLETED_TODAY timezone=%s local_date=%s",
                    timezone_name,
                    payload["local_date"],
                )
                if emit_stdout:
                    print(json.dumps(payload, ensure_ascii=False))
                return 0, payload

        with price_transport() as transport:
            with session_factory()() as session:
                if only_if_due:
                    due = due_supplier_ids(
                        session, current_time, timezone_name=timezone_name, item_id=item_id
                    )
                    if not due:
                        session.rollback()
                        payload = {
                            "status": "SKIPPED",
                            "reason": "ALREADY_COMPLETED_TODAY",
                            "timezone": timezone_name,
                            "local_date": current_time.astimezone(zone).date().isoformat(),
                        }
                        if emit_stdout:
                            print(json.dumps(payload, ensure_ascii=False))
                        return 0, payload
                items = (
                    [item_id]
                    if item_id
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
                for target_item_id in items:
                    run_time = now or datetime.now(UTC)
                    result = PriceService(session).refresh(target_item_id, run_time, transport)
                    output = PriceRefreshResponse.model_validate(result).model_dump(mode="json")
                    outputs.append(output)
                    if emit_stdout:
                        print(json.dumps(output, ensure_ascii=False))
                    item_statuses = [run.status for run in result["runs"]]
                    statuses.extend(item_statuses)
                    LOG.info(
                        "price_worker_item_refreshed item_id=%s runs=%s",
                        target_item_id,
                        item_statuses,
                    )
                    session.rollback()
    except WorkerBusyError:
        payload = {"status": "BUSY", "error_code": "PRICE_WORKER_BUSY"}
        LOG.warning("price_worker_busy error_code=PRICE_WORKER_BUSY")
        if emit_stdout:
            print(json.dumps(payload))
        return 3, payload
    except Exception as exc:
        code = "DATABASE_UNAVAILABLE" if isinstance(exc, SQLAlchemyError) else "WORKER_FAILED"
        payload = {"status": "ERROR", "error_code": code, "type": type(exc).__name__}
        LOG.error("price_worker_failed error_code=%s type=%s", code, type(exc).__name__)
        if emit_stdout:
            print(json.dumps(payload))
        return 1, payload

    if statuses and all(status == "OK" for status in statuses):
        return 0, {"status": "OK", "items": outputs, "run_statuses": statuses}
    exit_code = 2 if any(status in {"OK", "PARTIAL"} for status in statuses) else 1
    return exit_code, {
        "status": "PARTIAL" if exit_code == 2 else "ERROR",
        "items": outputs,
        "run_statuses": statuses,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--item-id", type=int)
    group.add_argument("--all-active", action="store_true")
    parser.add_argument(
        "--only-if-due",
        action="store_true",
        help="Run only if at least one active supplier has not completed an OK scrape today.",
    )
    parser.add_argument(
        "--timezone",
        default=os.getenv("STOCKSMART_DAILY_SCRAPE_TIMEZONE", DEFAULT_TIMEZONE),
        help="IANA timezone used to evaluate the daily schedule (default: America/Santiago).",
    )
    args = parser.parse_args(argv)
    if args.item_id is not None and args.item_id <= 0:
        parser.error("--item-id must be positive")
    try:
        resolve_timezone(args.timezone)
    except ValueError as exc:
        parser.error(str(exc))
    exit_code, _ = run_worker(
        item_id=args.item_id,
        only_if_due=args.only_if_due,
        timezone_name=args.timezone,
        emit_stdout=True,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
