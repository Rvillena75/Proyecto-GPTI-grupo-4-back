"""Catalog, real source-parser replay, persistent price history and full order receipt."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import func, select

from app.api.procurement_routes import get_price_transport
from app.main import app
from app.persistence.models import Item, Movement, Order, PriceQuote, Suggestion, SupplierProduct
from app.services.orders import OrderService
from scraper.contracts import ScrapeError
from scraper.store import worker_lock
from scraper.transport import PublicResponse

START = datetime(2026, 10, 1, 12, tzinfo=UTC)
NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "e2"


def catalog(api_client):
    client, set_time = api_client
    set_time(START)
    item = client.post(
        "/items",
        json={
            "name": "Pechuga asada",
            "base_unit": "kg",
            "initial_quantity": "10",
            "initial_unit": "kg",
        },
    ).json()
    source = json.loads((FIXTURES / "config" / "products.json").read_text(encoding="utf-8"))
    products = []
    for config in source["suppliers"]:
        supplier = client.post(
            "/suppliers",
            json={
                "name": config["name"],
                "lead_time_days": config["lead_time_days"],
                "adapter": config["adapter"],
            },
        )
        assert supplier.status_code == 201, supplier.text
        quantity = D(config["pack"]["content_quantity"]) / 1000
        product = client.post(
            f"/suppliers/{supplier.json()['id']}/products",
            json={
                "item_id": item["id"],
                "name_on_site": config["name_on_site"],
                "url": config["url"],
                "pack_quantity": str(quantity),
                "min_packs": config["min_packs"],
                "scrape_config": {
                    key: config[key] for key in ("sku", "brand", "expected_name_tokens", "pack")
                },
            },
        )
        assert product.status_code == 201, product.text
        products.append(product.json())
    set_time(START + timedelta(days=1))
    assert (
        client.post(
            "/movements",
            json={"item_id": item["id"], "kind": "EXIT", "quantity": "7", "unit": "kg"},
        ).status_code
        == 201
    )
    set_time(NOW)
    return client, set_time, item, products


class EvidenceTransport:
    def __init__(self, *, fail=None, observed=None, out_of_stock=False, before_fetch=None):
        self.responses = {}
        self.fail = fail
        self.observed = observed
        self.out_of_stock = out_of_stock
        self.before_fetch = before_fetch

    def fetch_product(self, url, adapter):
        if self.before_fetch:
            callback, self.before_fetch = self.before_fetch, None
            callback()
        if adapter == self.fail:
            raise ScrapeError("PUBLIC_REQUEST_FAILED", "Controlled public source failure")
        stem = (
            FIXTURES
            / "evidencia"
            / "2026-10-07"
            / "asada"
            / {
                "central": "central_asada_2500g",
                "jumbo": "jumbo_asada_125g",
                "santa_isabel": "santa_isabel_asada_125g",
            }[adapter]
        )
        metadata = json.loads(stem.with_suffix(".json").read_text(encoding="utf-8-sig"))
        body = stem.with_suffix(".html").read_text(encoding="utf-8")
        if self.out_of_stock:
            body = body.replace("InStock", "OutOfStock")
            body = body.replace('"isAvailable":true', '"isAvailable":false')
            body = body.replace(r"\"isAvailable\":true", r"\"isAvailable\":false")
        response = PublicResponse(
            body,
            self.observed or datetime.fromisoformat(metadata["observed_at"]),
            hashlib.sha256(body.encode()).hexdigest(),
            url,
        )
        self.responses[url] = response
        return response


def refresh(client, item_id, transport):
    app.dependency_overrides[get_price_transport] = lambda: transport
    try:
        response = client.post(f"/prices/{item_id}/refresh")
        assert response.status_code == 200, response.text
        return response.json()
    finally:
        app.dependency_overrides.pop(get_price_transport, None)


def approved_order(factory, item_id, product_id, *, status="APPROVED", packs=2):
    with factory() as session, session.begin():
        product = session.get(SupplierProduct, product_id)
        suggestion = Suggestion(
            item_id=item_id,
            status=status,
            required_qty=D(1),
            in_transit_qty=D(0),
            supplier_product_id=product_id,
            packs=packs,
            purchased_qty=product.pack_quantity * packs,
            total_clp=D(1000),
            inputs_snapshot={},
            created_at=NOW,
        )
        session.add(suggestion)
        session.flush()
        order = Order(
            suggestion_id=suggestion.id,
            code=f"OC-{suggestion.id:04d}",
            status="APPROVED",
            decided_at=NOW,
        )
        session.add(order)
        session.flush()
        return order.id


def assert_same_order(left, right):
    # JSON offsets can differ after PostgreSQL reload; compare the actual instants/decimals.
    def canonical(order):
        result = dict(order)
        for key in ("decided_at", "expected_at", "received_at"):
            if result[key] is not None:
                result[key] = datetime.fromisoformat(result[key])
        for key in ("purchased_qty", "total_clp", "received_quantity"):
            if result[key] is not None:
                result[key] = D(result[key])
        return result

    assert canonical(left) == canonical(right)


def test_supplier_crud_deactivates_without_erasing_history(api_client):
    client, _ = api_client
    first = client.post("/suppliers", json={"name": "Jumbo", "lead_time_days": 2})
    assert first.status_code == 201
    identifier = first.json()["id"]
    assert client.post("/suppliers", json={"name": "Jumbo", "lead_time_days": 3}).status_code == 409
    assert client.get(f"/suppliers/{identifier}").json()["name"] == "Jumbo"
    changed = client.patch(
        f"/suppliers/{identifier}", json={"lead_time_days": 4, "is_active": False}
    )
    assert changed.status_code == 200
    assert changed.json()["lead_time_days"] == 4 and changed.json()["is_active"] is False
    assert len(client.get("/suppliers").json()) == 1
    assert client.get("/suppliers/9999").status_code == 404
    assert client.patch(f"/suppliers/{identifier}", json={}).status_code == 422


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "   "},
        {"lead_time_days": -1},
        {"lead_time_days": True},
        {"lead_time_days": None},
        {"lead_time_days": 2147483648},
        {"is_active": None},
        {"price_source_url": "https://user:secret@example.test/"},
        {"price_source_url": "https://["},
    ],
)
def test_invalid_supplier_update_is_422_and_atomic(api_client, changes):
    client, _ = api_client
    identifier = client.post("/suppliers", json={"name": "A", "lead_time_days": 1}).json()["id"]
    assert client.patch(f"/suppliers/{identifier}", json=changes).status_code == 422
    assert client.get(f"/suppliers/{identifier}").json()["lead_time_days"] == 1


def test_products_validate_identity_format_foreign_keys_and_preserve_used_products(api_client):
    client, _, item, products = catalog(api_client)
    product = products[0]
    route = f"/suppliers/{product['supplier_id']}/products"
    body = {key: value for key, value in product.items() if key not in {"id", "supplier_id"}}
    assert client.post(route, json=body).status_code == 409
    assert client.post(route, json={**body, "item_id": 9999}).status_code == 404
    assert (
        client.post(
            route, json={**body, "url": "https://www.centralmayorista.cl/api/internal"}
        ).status_code
        == 422
    )
    assert client.post(route, json={**body, "pack_quantity": "2.5000001"}).status_code == 422
    assert client.post(route, json={**body, "pack_quantity": "3"}).status_code == 422
    assert client.get(route).json()[0]["item_id"] == item["id"]
    refresh(client, item["id"], EvidenceTransport())
    edit = f"{route}/{product['id']}"
    assert client.patch(edit, json={"scrape_config": None}).status_code == 409
    assert client.patch(edit, json={"is_active": False}).status_code == 200
    assert (
        client.patch(f"/suppliers/{product['supplier_id']}", json={"adapter": "jumbo"}).status_code
        == 409
    )
    assert (
        client.patch(
            f"/suppliers/{products[1]['supplier_id']}/products/{product['id']}",
            json={"is_active": False},
        ).status_code
        == 404
    )


def test_prices_persist_real_parsers_retries_and_original_validity(api_client, clean_db):
    client, set_time, item, products = catalog(api_client)
    original = refresh(client, item["id"], EvidenceTransport())
    assert len(original["offers"]) == 3
    assert all(offer["state"] == "CURRENT" for offer in original["offers"])
    assert original["comparison"]["recommended"] is not None
    assert D(original["required_quantity"]) > 0
    assert all(run["status"] == "OK" for run in original["runs"])
    again = refresh(client, item["id"], EvidenceTransport())
    assert [offer["quote_id"] for offer in original["offers"]] == [
        offer["quote_id"] for offer in again["offers"]
    ]
    with clean_db() as session:
        assert session.scalar(select(func.count()).select_from(PriceQuote)) == 3
    partial = refresh(client, item["id"], EvidenceTransport(fail="central"))
    assert partial["runs"][0]["status"] == "ERROR"
    assert partial["offers"][0]["state"] == "CURRENT"
    assert partial["offers"][0]["observed_at"] == original["offers"][0]["observed_at"]
    assert len(client.get(f"/suppliers/{products[0]['supplier_id']}/scrape-runs").json()) == 3
    set_time(NOW + timedelta(days=8))
    expired = client.get(f"/prices/{item['id']}").json()
    assert all(offer["state"] == "EXPIRED" for offer in expired["offers"])
    assert expired["comparison"]["recommended"] is None
    assert client.get("/prices/9999").status_code == 404


def test_unavailability_then_failure_never_revives_a_prior_available_quote(api_client):
    client, _, item, _ = catalog(api_client)
    refresh(client, item["id"], EvidenceTransport())
    refresh(client, item["id"], EvidenceTransport(observed=NOW, out_of_stock=True))
    final = refresh(
        client, item["id"], EvidenceTransport(fail="central", observed=NOW, out_of_stock=True)
    )
    assert final["offers"][0]["state"] == "OUT_OF_STOCK"
    assert final["comparison"]["recommended"] is None


def test_quote_does_not_use_configuration_changed_during_request(api_client, clean_db):
    client, _, item, products = catalog(api_client)

    def deactivate():
        with clean_db() as session, session.begin():
            session.get(SupplierProduct, products[0]["id"]).is_active = False

    result = refresh(client, item["id"], EvidenceTransport(before_fetch=deactivate))
    assert result["runs"][0]["status"] == "ERROR"
    assert result["offers"][0]["state"] == "INACTIVE"
    with clean_db() as session:
        assert session.scalar(select(func.count()).select_from(PriceQuote)) == 2


def test_only_one_current_supplier_cannot_be_recommended(api_client):
    client, _, item, products = catalog(api_client)
    refresh(client, item["id"], EvidenceTransport())
    for product in products[1:]:
        client.patch(f"/suppliers/{product['supplier_id']}", json={"is_active": False})
    response = client.get(f"/prices/{item['id']}").json()
    assert response["comparison"]["status"] == "INSUFFICIENT_CURRENT_PRICES"
    assert response["comparison"]["recommended"] is None


def test_missing_scraper_metadata_is_recorded_without_inventing_prices(api_client):
    client, _, item, products = catalog(api_client)
    product = products[0]
    client.patch(
        f"/suppliers/{product['supplier_id']}/products/{product['id']}",
        json={"scrape_config": None},
    )
    result = refresh(client, item["id"], EvidenceTransport())
    assert result["offers"][0]["state"] == "MISSING"
    assert result["runs"][0]["status"] == "ERROR"
    assert "SCRAPE_CONFIG_REQUIRED" in result["runs"][0]["message"]
    assert result["comparison"]["recommended"] is not None  # The other two suppliers still work.


def test_new_quotes_acquired_after_request_start_are_current(api_client):
    client, _, item, _ = catalog(api_client)
    result = refresh(client, item["id"], EvidenceTransport(observed=NOW + timedelta(seconds=2)))
    assert all(offer["state"] == "CURRENT" for offer in result["offers"])


def test_simultaneous_price_worker_returns_409_without_fetching(api_client, monkeypatch, tmp_path):
    client, _, item, _ = catalog(api_client)
    monkeypatch.setenv("STOCKSMART_PRICE_EVIDENCE_DIR", str(tmp_path))
    with worker_lock(tmp_path):
        response = client.post(f"/prices/{item['id']}/refresh")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PRICE_WORKER_BUSY"


def test_get_orders_and_receipt_persist_and_retry_without_double_stock(api_client, clean_db):
    client, _, item, products = catalog(api_client)
    assert client.get("/orders").json() == []
    order_id = approved_order(clean_db, item["id"], products[1]["id"])
    before = D(client.get(f"/items/{item['id']}").json()["current_stock"])
    history = client.get("/orders").json()
    assert history[0]["status"] == "APPROVED" and history[0]["receipt_movement_id"] is None
    first = client.post(f"/orders/{order_id}/receipt")
    assert first.status_code == 200, first.text
    saved = first.json()
    assert saved["status"] == "RECEIVED" and D(saved["received_quantity"]) == D("0.25")
    retry = client.post(f"/orders/{order_id}/receipt", json={})
    assert_same_order(retry.json(), saved)
    assert_same_order(client.get("/orders").json()[0], saved)
    assert D(client.get(f"/items/{item['id']}").json()["current_stock"]) == before + D("0.25")
    assert D(client.get(f"/items/{item['id']}").json()["assessment"]["in_transit_quantity"]) == 0
    assert (
        client.post(f"/orders/{order_id}/receipt", json={"received_quantity": "1"}).status_code
        == 409
    )
    assert (
        client.post(f"/movements/{saved['receipt_movement_id']}/reverse", json={}).status_code
        == 409
    )
    assert client.post("/orders/9999/receipt").status_code == 404
    with clean_db() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Movement).where(Movement.kind == "ENTRY")
            )
            == 1
        )


def test_variable_weight_requires_measured_quantity_and_preserves_nominal_order(
    api_client, clean_db
):
    client, _, item, products = catalog(api_client)
    order_id = approved_order(clean_db, item["id"], products[0]["id"])
    assert client.post(f"/orders/{order_id}/receipt").status_code == 422
    received = client.post(f"/orders/{order_id}/receipt", json={"received_quantity": "4.82"})
    assert received.status_code == 200, received.text
    assert D(received.json()["purchased_qty"]) == 5
    assert D(received.json()["received_quantity"]) == D("4.82")
    assert_same_order(client.post(f"/orders/{order_id}/receipt").json(), received.json())


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"received_at": START.isoformat()}, 422),
        ({"received_at": (NOW + timedelta(days=1)).isoformat()}, 422),
        ({"received_at": "2026-10-08T12:00:00"}, 422),
        ({"received_quantity": "0.125"}, 409),
        ({"received_quantity": "0.2500001"}, 422),
        ({"received_quantity": "1000000000000000000"}, 422),
    ],
)
def test_bad_receipt_does_not_mutate_order_or_inventory(api_client, clean_db, payload, expected):
    client, _, item, products = catalog(api_client)
    order_id = approved_order(clean_db, item["id"], products[1]["id"])
    assert client.post(f"/orders/{order_id}/receipt", json=payload).status_code == expected
    with clean_db() as session:
        assert session.get(Order, order_id).status == "APPROVED"
        assert session.get(Item, item["id"]).current_stock == 3


def test_rejected_suggestion_never_enters_inventory(api_client, clean_db):
    client, _, item, products = catalog(api_client)
    order_id = approved_order(clean_db, item["id"], products[1]["id"], status="REJECTED")
    assert client.post(f"/orders/{order_id}/receipt").status_code == 409


def test_receipt_failure_rolls_back_entry_stock_and_order(api_client, clean_db, monkeypatch):
    _, _, item, products = catalog(api_client)
    order_id = approved_order(clean_db, item["id"], products[1]["id"])

    def fail(*args):
        raise RuntimeError("Controlled failure after order and stock flush")

    monkeypatch.setattr(OrderService, "_view", fail)
    with clean_db() as session, pytest.raises(RuntimeError):
        OrderService(session).receive(order_id, NOW)
    with clean_db() as session:
        assert session.get(Order, order_id).status == "APPROVED"
        assert session.get(Item, item["id"]).current_stock == 3
        assert (
            session.scalar(
                select(func.count()).select_from(Movement).where(Movement.kind == "ENTRY")
            )
            == 0
        )


def test_concurrent_full_receipt_increments_stock_once(api_client, clean_db):
    _, _, item, products = catalog(api_client)
    order_id = approved_order(clean_db, item["id"], products[1]["id"])
    barrier = Barrier(2)

    def receive():
        barrier.wait(timeout=5)
        with clean_db() as session:
            return OrderService(session).receive(order_id, NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: receive(), range(2)))
    assert results[0]["receipt_movement_id"] == results[1]["receipt_movement_id"]
    with clean_db() as session:
        assert session.get(Item, item["id"]).current_stock == D("3.25")
