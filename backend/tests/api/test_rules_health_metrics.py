"""Independent DRS arithmetic, period metrics, input boundaries and readiness failures."""

import json
import socket
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_auth_security import warehouse
from test_operations import isolated_database
from test_procurement import EvidenceTransport, catalog, refresh

from app.domain.forecast import Consumption, assess_stock
from app.domain.replenishment import assess_replenishment
from app.main import app
from app.persistence.database import get_session
from app.services.inventory import InventoryService
from scraper.adaptadores.public_html import parse_product
from scraper.contracts import ScrapeError


@pytest.mark.parametrize(
    "stock,transit,projected,required,action",
    [
        ("1", "0", "-3", "17", "17"),
        ("9", "0", "5", "9", "9"),
        ("1", "2", "-3", "15", "0"),
        ("9", "9", "5", "0", "0"),
    ],
)
def test_both_evaluators_agree_with_drs(stock, transit, projected, required, action):
    summary = Consumption("ESTABLISHED", 28, D(28), D(7), None, None)
    now = datetime(2026, 10, 8, 12, tzinfo=UTC)
    first = assess_stock(D(stock), D(5), summary, now, 4, D(transit))
    second = assess_replenishment(
        D(stock), D(5), summary, now, lead_time_days=4, in_transit_quantity=D(transit)
    )
    for result in (first, second):
        assert result.projected_stock_at_arrival == D(projected)
        assert result.required_quantity == D(required)
        assert result.suggested_quantity == D(action)
        assert result.order_by_at == now + timedelta(days=int(stock) - 5 - 4)
    assert second.projected_arrival_stock == first.projected_stock_at_arrival


def test_drs_seventeen_kg_is_used_by_http_and_price_comparison(api_client):
    client, clock = api_client
    start = datetime(2026, 9, 1, tzinfo=UTC)
    clock(start)
    item = client.post(
        "/items",
        json={
            "name": "DRS formula",
            "base_unit": "kg",
            "timezone": "UTC",
            "initial_quantity": "29",
            "initial_unit": "kg",
            "manual_safe_stock": "5",
        },
    ).json()
    clock(start + timedelta(days=1))
    assert (
        client.post(
            "/movements",
            json={"item_id": item["id"], "kind": "EXIT", "quantity": "28", "unit": "kg"},
        ).status_code
        == 201
    )
    for name, lead in (("Source A", 2), ("Source B", 4)):
        supplier = client.post("/suppliers", json={"name": name, "lead_time_days": lead}).json()
        assert (
            client.post(
                f"/suppliers/{supplier['id']}/products",
                json={
                    "item_id": item["id"],
                    "name_on_site": "Pack",
                    "url": f"https://example.test/product-{lead}",
                    "pack_quantity": "1",
                },
            ).status_code
            == 201
        )
    clock(start + timedelta(days=28, hours=12))
    state = client.get(f"/items/{item['id']}").json()
    assert D(state["consumption"]["weekly_average"]) == 7
    assert D(state["assessment"]["projected_stock_at_arrival"]) == -3
    assert D(state["assessment"]["suggested_quantity"]) == 17
    prices = client.get(f"/prices/{item['id']}").json()
    assert D(prices["required_quantity"]) == 17
    assert prices["comparison"]["recommended"] is None  # No fabricated current quotes.


@pytest.mark.parametrize("base_unit", ["kg", "L", "unidad"])
@pytest.mark.parametrize(
    "physical,review,shortage", [("12", True, "0"), ("10", False, "0"), ("8", False, "2")]
)
def test_review_and_alert_unit_are_domain_independent(
    api_client, base_unit, physical, review, shortage
):
    client, _ = api_client
    item = client.post(
        "/items",
        json={
            "name": "Units",
            "base_unit": base_unit,
            "initial_quantity": "10",
            "initial_unit": base_unit,
            "manual_safe_stock": "20",
        },
    ).json()
    count = client.post(
        "/counts", json={"item_id": item["id"], "physical_quantity": physical, "unit": base_unit}
    )
    assert count.status_code == 201 and count.json()["needs_review"] is review
    assert D(count.json()["shortage"]) == D(shortage)
    assert client.get(f"/items/{item['id']}/counts").json()[0]["needs_review"] is review
    assert client.get("/alerts").json()[0]["base_unit"] == base_unit


def test_metrics_exact_period_denominators_and_missing_comparison(api_client):
    client, clock = api_client
    start = datetime(2026, 10, 1, tzinfo=UTC)
    clock(start)
    item = client.post(
        "/items",
        json={
            "name": "Metrics",
            "base_unit": "kg",
            "timezone": "UTC",
            "initial_quantity": "20",
            "initial_unit": "kg",
        },
    ).json()
    clock(start + timedelta(hours=1))
    client.post(
        "/movements", json={"item_id": item["id"], "kind": "ENTRY", "quantity": "10", "unit": "kg"}
    )
    clock(start + timedelta(hours=2))
    client.post("/counts", json={"item_id": item["id"], "physical_quantity": "32", "unit": "kg"})
    clock(start + timedelta(hours=3))
    client.post("/counts", json={"item_id": item["id"], "physical_quantity": "29", "unit": "kg"})
    period = {"start": start.isoformat(), "end": (start + timedelta(days=1)).isoformat()}
    response = client.get("/metrics", params=period)
    assert response.status_code == 200, response.text
    result = response.json()
    assert D(result["items"][0]["shortage"]["rate_percent"]) == D("9.375")
    assert result["approved_orders"] == 0 and result["stockouts"] == 0
    assert result["estimated_savings_percent"] is None
    assert result["estimated_savings_clp"] is None
    assert result["alerts_attended_before_depletion"] is None
    assert result["impact_comparison"]["status"] == "NOT_VERIFIABLE"
    assert (
        client.get("/metrics", params={"start": period["end"], "end": period["start"]}).status_code
        == 422
    )
    assert (
        client.get("/metrics", params={"start": "2026-10-01", "end": "2026-10-02"}).status_code
        == 422
    )


def test_metrics_order_savings_are_frozen_and_retries_do_not_double_count(api_client):
    client, clock, item, _ = catalog(api_client)
    refresh(client, item["id"], EvidenceTransport())
    suggestion = client.post("/suggestions", json={"item_id": item["id"]}).json()
    endpoint = f"/suggestions/{suggestion['id']}/decision"
    assert client.post(endpoint, json={"decision": "APPROVE"}).status_code == 200
    assert client.post(endpoint, json={"decision": "APPROVE"}).status_code == 200
    from test_procurement import NOW

    result = client.get(
        "/metrics", params={"start": NOW.isoformat(), "end": (NOW + timedelta(days=1)).isoformat()}
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["approved_orders"] == 1
    assert D(body["estimated_savings_clp"]) == D(suggestion["savings_clp"])
    assert (
        D(body["estimated_savings_percent"])
        == D(suggestion["savings_clp"]) / D(suggestion["next_best_total_clp"]) * 100
    )
    assert body["alerts_attended_before_depletion"] == 1


def test_stockout_transitions_period_boundaries_and_invalidated_records(api_client):
    client, clock = api_client
    start = datetime(2026, 10, 1, tzinfo=UTC)
    clock(start)
    item = client.post(
        "/items",
        json={
            "name": "Observed stockouts",
            "base_unit": "kg",
            "timezone": "UTC",
            "initial_quantity": "5",
            "initial_unit": "kg",
        },
    ).json()
    clock(start + timedelta(days=1))
    first = client.post(
        "/movements", json={"item_id": item["id"], "kind": "EXIT", "quantity": "5", "unit": "kg"}
    ).json()
    clock(start + timedelta(days=2))
    client.post(
        "/movements", json={"item_id": item["id"], "kind": "ENTRY", "quantity": "5", "unit": "kg"}
    )
    clock(start + timedelta(days=3))
    second = client.post(
        "/movements", json={"item_id": item["id"], "kind": "EXIT", "quantity": "5", "unit": "kg"}
    ).json()
    for low, high, movement in ((0, 3, first), (3, 4, second)):
        result = client.get(
            "/metrics",
            params={
                "start": (start + timedelta(days=low)).isoformat(),
                "end": (start + timedelta(days=high)).isoformat(),
            },
        ).json()
        assert result["stockouts"] == 1
        assert result["items"][0]["stockout_movement_ids"] == [movement["id"]]
    clock(start + timedelta(days=4))
    assert client.post(f"/movements/{first['id']}/reverse", json={}).status_code == 201
    result = client.get(
        "/metrics",
        params={"start": start.isoformat(), "end": (start + timedelta(days=4)).isoformat()},
    ).json()
    assert result["stockouts"] == 0


def test_warehouse_cannot_read_metrics(api_client, clean_db, test_password_hash):
    client, _ = api_client
    warehouse(client, clean_db, test_password_hash)
    assert (
        client.get(
            "/metrics", params={"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z"}
        ).status_code
        == 403
    )


def test_legacy_orders_without_comparables_do_not_invent_totals(api_client, clean_db):
    from sqlalchemy import text
    from test_procurement import NOW, approved_order

    client, _, item, products = catalog(api_client)
    approved_order(clean_db, item["id"], products[0]["id"])
    with clean_db() as session, session.begin():
        session.execute(text("UPDATE suggestions SET total_clp=NULL"))
    result = client.get(
        "/metrics", params={"start": NOW.isoformat(), "end": (NOW + timedelta(days=1)).isoformat()}
    )
    assert result.status_code == 200
    body = result.json()
    assert body["approved_orders"] == 1
    assert body["approved_total_clp"] is None and body["estimated_savings_clp"] is None
    assert body["estimated_savings_percent"] is None
    assert body["alerts_attended_before_depletion"] is None


def test_empty_database_is_not_ready_and_recovery_is_visible(api_client, clean_db):
    client, _ = api_client
    original = app.dependency_overrides[get_session]
    with isolated_database(clean_db) as (empty, _):

        def no_schema():
            with Session(empty) as session:
                yield session

        app.dependency_overrides[get_session] = no_schema
        assert client.get("/ready").status_code == 503
        assert client.get("/health").status_code == 503
        assert client.get("/live").status_code == 200
    app.dependency_overrides[get_session] = original
    assert client.get("/ready").status_code == 200


@pytest.mark.parametrize("commit", [None, "a" * 40, "sensitive-invalid-value"])
def test_deployed_version_does_not_invent_a_commit_or_echo_invalid_metadata(
    api_client, monkeypatch, commit
):
    client, _ = api_client
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.delenv("STOCKSMART_BUILD_SHA", raising=False)
    if commit is not None:
        monkeypatch.setenv("RENDER_GIT_COMMIT", commit)
    result = client.get("/version")
    assert result.status_code == 200
    assert result.json()["commit"] == (commit if commit == "a" * 40 else None)
    assert "sensitive-invalid-value" not in result.text


def test_unresponsive_database_is_bounded(api_client):
    client, _ = api_client
    original = app.dependency_overrides[get_session]
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    finished = threading.Event()

    def blackhole():
        connection, _ = listener.accept()
        with connection:
            finished.wait(8)

    thread = threading.Thread(target=blackhole, daemon=True)
    thread.start()
    engine = create_engine(
        f"postgresql+psycopg://test@127.0.0.1:{port}/isolated_test",
        connect_args={"connect_timeout": 2},
    )

    def blocked():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = blocked
    try:
        started = time.monotonic()
        response = client.get("/ready")
        assert response.status_code == 503
        assert time.monotonic() - started < 5
    finally:
        app.dependency_overrides[get_session] = original
        finished.set()
        listener.close()
        thread.join(timeout=2)
        engine.dispose()


def test_all_errors_use_sanitized_json(api_client, monkeypatch, caplog):
    client, _ = api_client
    assert client.get("/does-not-exist").json()["error"]["code"] == "HTTP_404"
    assert client.delete("/items").json()["error"]["code"] == "HTTP_405"

    def explode(*args):
        raise ValueError("sensitive-exception-marker")

    monkeypatch.setattr(InventoryService, "items", explode)
    with TestClient(app, raise_server_exceptions=False) as isolated:
        response = isolated.get(
            "/items", headers={"Authorization": client.headers["Authorization"]}
        )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert response.headers["X-Request-ID"]
    assert "sensitive-exception-marker" not in response.text + caplog.text


@pytest.mark.parametrize(
    "field,value",
    [
        ("offers", None),
        ("brand", []),
        ("url", {}),
        ("name", None),
        ("offers", {"@type": "AggregateOffer"}),
    ],
)
def test_malformed_commercial_metadata_is_typed(field, value):
    supplier = {"adapter": "jumbo", "sku": "123"}
    product = {
        "@type": "Product",
        "sku": "123",
        "name": "Test",
        "brand": "Test",
        "offers": {
            "@type": "Offer",
            "price": 1000,
            "priceCurrency": "CLP",
            "availability": "https://schema.org/InStock",
        },
    }
    product[field] = value
    body = '<script type="application/ld+json">' + json.dumps(product) + "</script>"
    with pytest.raises(ScrapeError):
        parse_product(body, "https://www.jumbo.cl/test/p", supplier)
