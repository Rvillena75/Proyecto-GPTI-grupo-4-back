"""Regression tests for daily automatic price scraping and user-facing movement receipt notes."""

from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, update
from test_procurement import NOW, START, EvidenceTransport, approved_order, catalog

from app.e2.contracts import ApprovedOrder, PackSpec
from app.e2.receipts import ReceiptService
from app.persistence.models import Movement, Order, PriceQuote, ScrapeRun, Suggestion
from app.services.inventory import format_movement_note
from scraper import postgres, scheduler


@pytest.mark.parametrize(
    "kind,raw_note,expected",
    [
        (
            "ENTRY",
            (
                'E2_RECEIPT:{"approved_at":"2026-09-30T12:05:00+00:00","base_unit":"kg",'
                '"order_code":"OC-E2-0015","order_id":"OC-E2-0015","receipt_id":"RCP-E2-0015"}'
            ),
            "Recepción OC-E2-0015",
        ),
        (
            "ENTRY",
            'E2_RECEIPT:{"receipt_id":"RCP-E2-0007"}',
            "Recepción RCP-E2-0007",
        ),
        (
            "ENTRY",
            'E2_RECEIPT:{"order_id":42}',
            "Recepción 42",
        ),
        ("ENTRY", "E2_RECEIPT:{}", None),
        ("ENTRY", "E2_RECEIPT:{not-json", None),
        ("ENTRY", "Order receipt OC-0013", "Recepción OC-0013"),
        ("ENTRY", "Recepción OC-0013", "Recepción OC-0013"),
        ("EXIT", "Consumo cocina", "Consumo cocina"),
        ("ENTRY", "   ", None),
        ("ENTRY", None, None),
        (
            "INITIAL",
            "SYNTHETIC_E2_TEST_DATA_NOT_A_REAL_BUSINESS; Dataset_v2 sha256=abc",
            "SYNTHETIC_E2_TEST_DATA_NOT_A_REAL_BUSINESS; Dataset_v2 sha256=abc",
        ),
    ],
)
def test_format_movement_note_produces_clean_descriptions(kind, raw_note, expected):
    assert format_movement_note(kind, raw_note) == expected


def test_e2_and_order_receipts_display_clean_detail_while_preserving_db_metadata(
    api_client, clean_db
):
    client, _, item, products = catalog(api_client)
    order = ApprovedOrder(
        id="OC-E2-0015",
        code="OC-E2-0015",
        status="APPROVED",
        supplier_id=products[0]["supplier_id"],
        supplier_product_id=products[0]["id"],
        item_id=item["id"],
        packs=2,
        pack=PackSpec(
            commercial_format="bolsa",
            content_quantity=D("2.5"),
            content_unit="kg",
            units_per_pack=1,
            base_unit="kg",
            is_variable_weight=True,
        ),
        issued_at=START,
        approved_at=START,
        expected_at=NOW,
        lead_time_days=7,
    )
    with clean_db() as session:
        first = ReceiptService(session).receive(
            order,
            "RCP-E2-0015",
            NOW,
            date(2026, 10, 5),
            12,
            NOW,
            actual_received_quantity=D("4.95"),
        )
        raw_db_note = session.get(Movement, first.movement_id).note
        session.rollback()
        assert raw_db_note is not None and raw_db_note.startswith("E2_RECEIPT:{")
        # Idempotent retry still works because raw E2_RECEIPT metadata is intact in PostgreSQL.
        replay = ReceiptService(session).receive(
            order.model_copy(update={"status": "RECEIVED"}),
            "RCP-E2-0015",
            NOW,
            date(2026, 10, 5),
            12,
            NOW,
            actual_received_quantity=D("4.95"),
        )
        assert replay.idempotent_replay and replay.movement_id == first.movement_id

    order_id = approved_order(clean_db, item["id"], products[1]["id"])
    receipt_resp = client.post(f"/orders/{order_id}/receipt", json={})
    assert receipt_resp.status_code == 200, receipt_resp.text

    movements = client.get(f"/items/{item['id']}/movements").json()
    entries = [m for m in movements if m["kind"] == "ENTRY"]
    assert [m["note"] for m in entries] == ["Recepción OC-E2-0015", "Recepción OC-0001"]
    assert all("E2_RECEIPT:" not in (m["note"] or "") for m in entries)


def test_daily_scheduler_and_worker_update_last_scrape_respect_chile_tz_and_isolate_errors(
    api_client, clean_db, monkeypatch
):
    client, _, item, products = catalog(api_client)
    supplier_ids = [p["supplier_id"] for p in products]
    chile_tz = ZoneInfo("America/Santiago")
    now_utc = datetime.now(UTC)

    # Initially, all 3 suppliers have no runs ("Sin corridas").
    for sid in supplier_ids:
        assert client.get(f"/suppliers/{sid}/scrape-runs").json() == []

    with clean_db() as session:
        assert postgres.has_unscraped_suppliers(session) is True
        assert postgres.due_supplier_ids(session, now_utc) == sorted(supplier_ids)
        assert scheduler.should_trigger_daily_scrape(session, now_utc, target_hour=23) == (
            True,
            "INITIAL_MISSING_RUNS",
        )

    @contextmanager
    def ok_transport():
        yield EvidenceTransport(observed=now_utc)

    monkeypatch.setattr(postgres, "session_factory", lambda: clean_db)
    monkeypatch.setattr(scheduler, "session_factory", lambda: clean_db)
    monkeypatch.setattr(postgres, "price_transport", ok_transport)

    daily = scheduler.DailyPriceScheduler(timezone_name="America/Santiago", target_hour=7)
    code, result = daily.run_once(now=now_utc)
    assert code == 0 and result["status"] == "OK"
    assert result["trigger_reason"] == "INITIAL_MISSING_RUNS"

    # All 3 suppliers now have an OK ScrapeRun and CURRENT quotes; no orders or suggestions created.
    for sid in supplier_ids:
        runs = client.get(f"/suppliers/{sid}/scrape-runs").json()
        assert len(runs) == 1
        assert runs[0]["status"] == "OK"
        assert runs[0]["finished_at"] is not None

    with clean_db() as session:
        assert session.scalar(select(func.count(PriceQuote.id))) == 3
        assert session.scalar(select(func.count(Order.id))) == 0
        assert session.scalar(select(func.count(Suggestion.id))) == 0

    state = client.get(f"/items/{item['id']}").json()
    assert state["assessment"]["alert_status"] == "LOW_STOCK"

    # Re-running on the same Chile calendar day skips execution without duplicating runs.
    assert postgres.main(["--all-active", "--only-if-due", "--timezone", "America/Santiago"]) == 0
    code_same_day, payload_same_day = daily.run_once(now=now_utc)
    assert code_same_day == 0 and payload_same_day["status"] == "SKIPPED"
    assert payload_same_day["reason"] == "ALREADY_COMPLETED_TODAY"
    with clean_db() as session:
        assert session.scalar(select(func.count(ScrapeRun.id))) == 3

    # Verify Chile timezone boundary: if existing runs finished yesterday in America/Santiago,
    # today's daily run becomes due again at/after target_hour.
    yesterday_utc = now_utc - timedelta(days=1)
    with clean_db() as session, session.begin():
        session.execute(
            update(ScrapeRun).values(started_at=yesterday_utc, finished_at=yesterday_utc)
        )
        assert postgres.due_supplier_ids(session, now_utc, timezone_name="America/Santiago") == (
            sorted(supplier_ids)
        )

    @contextmanager
    def partial_transport():
        yield EvidenceTransport(fail="central", observed=now_utc + timedelta(seconds=5))

    monkeypatch.setattr(postgres, "price_transport", partial_transport)
    daily_due_now = scheduler.DailyPriceScheduler(
        timezone_name="America/Santiago",
        target_hour=now_utc.astimezone(chile_tz).hour,
    )
    code_partial, payload_partial = daily_due_now.run_once(now=now_utc + timedelta(seconds=5))
    assert code_partial == 2 and payload_partial["status"] == "PARTIAL"

    # Only the failed supplier ("central") remains due for today; the other two succeeded today.
    with clean_db() as session:
        due_after_partial = postgres.due_supplier_ids(
            session, now_utc + timedelta(seconds=10), timezone_name="America/Santiago"
        )
        assert due_after_partial == [products[0]["supplier_id"]]
