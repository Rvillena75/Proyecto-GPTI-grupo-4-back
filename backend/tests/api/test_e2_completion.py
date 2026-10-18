"""Real backend workflow over PostgreSQL; source HTML is explicit dated test evidence."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal as D
from threading import Barrier

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from test_procurement import NOW, EvidenceTransport, catalog, refresh

from app.domain.errors import RuleViolation
from app.persistence.models import Movement, Order, PriceQuote, ScrapeRun, Suggestion
from app.services.suggestions import SuggestionService
from scraper.transport import PublicResponse


def prepared(api_client):
    client, clock, item, products = catalog(api_client)
    refresh(client, item["id"], EvidenceTransport())
    return client, clock, item, products


def generate(client, item_id):
    response = client.post("/suggestions", json={"item_id": item_id})
    assert response.status_code == 201, response.text
    return response.json()


def test_complete_http_generation_approval_receipt(api_client, clean_db):
    client, clock, item, products = prepared(api_client)
    before = D(client.get(f"/items/{item['id']}").json()["current_stock"])
    suggestion = generate(client, item["id"])
    assert suggestion["status"] == "PENDING" and suggestion["created_by"] == 1
    assert client.get(f"/suggestions/{suggestion['id']}").json() == suggestion
    assert generate(client, item["id"])["id"] == suggestion["id"]
    assert client.get("/orders").json() == []
    approved = client.post(
        f"/suggestions/{suggestion['id']}/decision", json={"decision": "APPROVE"}
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["decided_by"] == 1
    assert approved.json()["inputs_snapshot"] == suggestion["inputs_snapshot"]
    order = client.get("/orders").json()[0]
    assert order["decided_by"] == 1
    assert D(client.get(f"/items/{item['id']}").json()["current_stock"]) == before
    assert (
        client.post("/suggestions", json={"item_id": item["id"]}).json()["error"]["code"]
        == "ORDER_IN_TRANSIT"
    )
    assert client.get(f"/prices/{item['id']}").json()["comparison"]["status"] == "IN_TRANSIT"
    assert (
        client.post(
            f"/suggestions/{suggestion['id']}/decision", json={"decision": "APPROVE"}
        ).status_code
        == 200
    )
    assert len(client.get("/orders").json()) == 1
    product = next(p for p in products if p["id"] == suggestion["supplier_product_id"])
    variable = product["scrape_config"]["pack"].get("is_variable_weight", False)
    quantity = D(suggestion["purchased_qty"]) + (D("0.02") if variable else D(0))
    receipt = client.post(
        f"/orders/{order['id']}/receipt", json={"received_quantity": str(quantity)}
    )
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["status"] == "RECEIVED"
    assert (
        client.post(
            f"/orders/{order['id']}/receipt", json={"received_quantity": str(quantity)}
        ).status_code
        == 200
    )
    assert D(client.get(f"/items/{item['id']}").json()["current_stock"]) == before + quantity
    movement = client.get(f"/items/{item['id']}/movements").json()[-1]
    assert movement["created_by"]["id"] == 1
    with clean_db() as session:
        assert session.scalar(select(func.count(Order.id))) == 1
        assert session.scalar(select(func.count(Movement.id)).where(Movement.kind == "ENTRY")) == 1


def test_rejection_requires_reason_and_is_immutable(api_client):
    client, _, item, _ = prepared(api_client)
    suggestion = generate(client, item["id"])
    url = f"/suggestions/{suggestion['id']}/decision"
    for reason in (None, "", "  "):
        assert client.post(url, json={"decision": "REJECT", "reason": reason}).status_code == 422
    first = client.post(url, json={"decision": "REJECT", "reason": " Sin presupuesto "})
    assert first.status_code == 200
    assert first.json()["rejection_reason"] == "Sin presupuesto"
    assert (
        client.post(url, json={"decision": "REJECT", "reason": "Sin presupuesto"}).json()
        == first.json()
    )
    assert client.post(url, json={"decision": "APPROVE"}).status_code == 409
    assert client.post(url, json={"decision": "REJECT", "reason": "Otro motivo"}).status_code == 409
    assert client.get("/orders").json() == []
    assert generate(client, item["id"])["id"] != suggestion["id"]


def test_legacy_incomplete_approval_is_not_a_successful_retry(api_client, clean_db):
    client, _, item, _ = prepared(api_client)
    suggestion = generate(client, item["id"])
    endpoint = f"/suggestions/{suggestion['id']}/decision"
    assert client.post(endpoint, json={"decision": "APPROVE"}).status_code == 200
    # Controlled corruption of this isolated test DB models an incomplete legacy integration.
    with clean_db() as session, session.begin():
        session.execute(text("DELETE FROM orders"))
    result = client.post(endpoint, json={"decision": "APPROVE"})
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "DECISION_STATE_CONFLICT"


@pytest.mark.parametrize(
    "decisions", [("APPROVE", "REJECT"), ("APPROVE", "APPROVE"), ("REJECT", "REJECT")]
)
def test_generation_and_decision_are_serialized(api_client, clean_db, decisions):
    client, _, item, _ = prepared(api_client)
    barrier = Barrier(2)

    def create(_):
        with clean_db() as session:
            barrier.wait()
            return SuggestionService(session, 1).generate(item["id"], NOW).id

    with ThreadPoolExecutor(2) as pool:
        ids = list(pool.map(create, range(2)))
    assert ids[0] == ids[1]
    barrier = Barrier(2)

    def decide(decision):
        with clean_db() as session:
            barrier.wait()
            try:
                return (
                    SuggestionService(session, 1)
                    .decide(ids[0], decision, "No" if decision == "REJECT" else None, NOW)
                    .status
                )
            except RuleViolation as exc:
                return exc.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(decide, decisions))
    if decisions[0] != decisions[1]:
        assert "DECISION_CONFLICT" in results
    else:
        assert results[0] == results[1]
    with clean_db() as session:
        approved = session.get(Suggestion, ids[0]).status == "APPROVED"
        assert session.scalar(select(func.count(Order.id))) == int(approved)


def test_snapshot_uses_lead_time_after_catalog_lock(api_client, clean_db, monkeypatch):
    client, _, item, _ = prepared(api_client)
    original = SuggestionService._lock_catalog
    changed = []

    def concurrent_change(service, item_id):
        if not changed:
            with clean_db() as session, session.begin():
                session.execute(text("UPDATE suppliers SET lead_time_days=lead_time_days+10"))
            changed.append(True)
        original(service, item_id)

    monkeypatch.setattr(SuggestionService, "_lock_catalog", concurrent_change)
    suggestion = generate(client, item["id"])
    snapshot = suggestion["inputs_snapshot"]
    state = client.get(f"/items/{item['id']}").json()
    assert (
        snapshot["inventory"]["assessment"]["lead_time_days"]
        == state["assessment"]["lead_time_days"]
    )
    assert D(suggestion["required_qty"]) == D(snapshot["prices"]["required_quantity"])
    needed = D(snapshot["inventory"]["assessment"]["suggested_quantity"])
    assert needed <= D(suggestion["required_qty"]) < needed + D("0.000001")


def test_decision_rolls_back_when_order_cannot_be_persisted(api_client, clean_db):
    from fastapi.testclient import TestClient
    from sqlalchemy import event

    from app.main import app

    client, _, item, _ = prepared(api_client)
    suggestion = generate(client, item["id"])

    def fail_order(*args):
        raise RuntimeError("controlled order persistence failure")

    event.listen(Order, "before_insert", fail_order)
    try:
        with TestClient(app, raise_server_exceptions=False) as isolated:
            response = isolated.post(
                f"/suggestions/{suggestion['id']}/decision",
                json={"decision": "APPROVE"},
                headers={"Authorization": client.headers["Authorization"]},
            )
            assert response.status_code == 500
    finally:
        event.remove(Order, "before_insert", fail_order)
    with clean_db() as session:
        assert session.get(Suggestion, suggestion["id"]).status == "PENDING"
        assert session.scalar(select(func.count(Order.id))) == 0


def test_receipt_uses_original_product_version_and_warehouse_actor(
    api_client, clean_db, test_password_hash
):
    from test_auth_security import warehouse

    client, _, item, products = prepared(api_client)
    suggestion = generate(client, item["id"])
    assert (
        client.post(
            f"/suggestions/{suggestion['id']}/decision", json={"decision": "APPROVE"}
        ).status_code
        == 200
    )
    order = client.get("/orders").json()[0]
    old = next(p for p in products if p["id"] == suggestion["supplier_product_id"])
    endpoint = f"/suppliers/{old['supplier_id']}/products"
    assert client.patch(endpoint + f"/{old['id']}", json={"is_active": False}).status_code == 200
    payload = {k: v for k, v in old.items() if k not in {"id", "supplier_id"}}
    payload["is_active"] = True
    assert client.post(endpoint, json=payload).status_code == 201
    actor = warehouse(client, clean_db, test_password_hash)
    receipt = client.post(
        f"/orders/{order['id']}/receipt", json={"received_quantity": suggestion["purchased_qty"]}
    )
    assert receipt.status_code == 200
    assert receipt.json()["supplier_product_id"] == old["id"]
    assert client.get(f"/items/{item['id']}/movements").json()[-1]["created_by"]["id"] == actor.id


@pytest.mark.parametrize("change", ["stock", "expiry", "inactive", "minimum"])
def test_stale_suggestion_cannot_be_approved(api_client, change):
    client, clock, item, products = prepared(api_client)
    suggestion = generate(client, item["id"])
    if change == "stock":
        assert (
            client.post(
                "/movements",
                json={"item_id": item["id"], "kind": "ENTRY", "quantity": "1", "unit": "kg"},
            ).status_code
            == 201
        )
    if change == "expiry":
        clock(NOW + timedelta(days=8))
    if change == "inactive":
        product = next(p for p in products if p["id"] == suggestion["supplier_product_id"])
        assert (
            client.patch(
                f"/suppliers/{product['supplier_id']}/products/{product['id']}",
                json={"is_active": False},
            ).status_code
            == 200
        )
    if change == "minimum":
        product = next(p for p in products if p["id"] == suggestion["supplier_product_id"])
        assert (
            client.patch(
                f"/suppliers/{product['supplier_id']}/products/{product['id']}",
                json={"min_packs": product["min_packs"] + 1},
            ).status_code
            == 200
        )
    assert (
        client.post(
            f"/suggestions/{suggestion['id']}/decision", json={"decision": "APPROVE"}
        ).status_code
        == 409
    )
    assert client.get("/orders").json() == []


@pytest.mark.parametrize("bad", [None, 17, [None, 1], {"x": "Product"}])
def test_invalid_jsonld_isolated_and_runs_closed(api_client, clean_db, bad):
    client, _, item, _ = catalog(api_client)

    class Unexpected(EvidenceTransport):
        def fetch_product(self, url, adapter):
            if adapter != "central":
                return super().fetch_product(url, adapter)
            body = '<script type="application/ld+json">' + json.dumps({"@type": bad}) + "</script>"
            result = PublicResponse(body, NOW, hashlib.sha256(body.encode()).hexdigest(), url)
            self.responses[url] = result
            return result

    output = refresh(client, item["id"], Unexpected())
    assert sorted(run["status"] for run in output["runs"]) == ["ERROR", "OK", "OK"]
    assert all(run["finished_at"] is not None for run in output["runs"])
    with clean_db() as session:
        assert session.scalar(select(func.count(PriceQuote.id))) == 2
        assert (
            session.scalar(select(func.count(ScrapeRun.id)).where(ScrapeRun.status == "RUNNING"))
            == 0
        )


def test_evidence_snapshot_survives_transport_and_has_hash(api_client, clean_db):
    client, _, item, _ = prepared(api_client)
    with clean_db() as session:
        quotes = list(session.scalars(select(PriceQuote)))
        assert len(quotes) == 3
        for quote in quotes:
            payload = json.dumps(
                quote.evidence_snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            )
            assert hashlib.sha256(payload.encode()).hexdigest() == quote.evidence_sha256
            assert quote.evidence_snapshot["raw"]["currency"] == "CLP"
            assert "<script" not in payload


def test_product_with_history_can_be_replaced_same_url(api_client, clean_db):
    client, _, item, products = prepared(api_client)
    old = products[0]
    endpoint = f"/suppliers/{old['supplier_id']}/products"
    assert client.patch(endpoint + f"/{old['id']}", json={"is_active": False}).status_code == 200
    payload = {k: v for k, v in old.items() if k not in {"id", "supplier_id"}}
    payload["is_active"] = True
    new = client.post(endpoint, json=payload)
    assert new.status_code == 201 and new.json()["id"] != old["id"]
    assert client.post(endpoint, json=payload).status_code == 409
    assert client.patch(endpoint + f"/{old['id']}", json={"is_active": True}).status_code == 409
    refresh(client, item["id"], EvidenceTransport())
    offers = client.get(f"/prices/{item['id']}").json()["offers"]
    assert next(o for o in offers if o["supplier_product_id"] == old["id"])["state"] == "INACTIVE"
    with clean_db() as session:
        assert (
            session.scalar(
                select(func.count(PriceQuote.id)).where(PriceQuote.supplier_product_id == old["id"])
            )
            == 1
        )


@pytest.mark.parametrize("field", ["received_at", "receipt_movement_id"])
def test_database_rejects_half_receipts(api_client, clean_db, field):
    client, _, item, _ = prepared(api_client)
    suggestion = generate(client, item["id"])
    assert (
        client.post(
            f"/suggestions/{suggestion['id']}/decision", json={"decision": "APPROVE"}
        ).status_code
        == 200
    )
    value = "clock_timestamp()" if field == "received_at" else "(SELECT min(id) FROM movements)"
    with clean_db() as session, pytest.raises(IntegrityError):
        session.execute(text(f"UPDATE orders SET {field}={value}"))
        session.commit()


def test_no_history_cannot_produce_fake_suggestion(api_client, clean_db):
    client, _ = api_client
    item = client.post(
        "/items",
        json={
            "name": "New",
            "base_unit": "kg",
            "initial_quantity": "1",
            "initial_unit": "kg",
            "manual_safe_stock": "5",
        },
    ).json()
    response = client.post("/suggestions", json={"item_id": item["id"]})
    assert (
        response.status_code == 409 and response.json()["error"]["code"] == "INSUFFICIENT_HISTORY"
    )
    with clean_db() as session:
        assert session.scalar(select(func.count(Suggestion.id))) == 0


def test_one_current_supplier_is_not_a_comparison(api_client, clean_db):
    client, _, item, products = prepared(api_client)
    for product in products[1:]:
        assert (
            client.patch(
                f"/suppliers/{product['supplier_id']}", json={"is_active": False}
            ).status_code
            == 200
        )
    response = client.post("/suggestions", json={"item_id": item["id"]})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INSUFFICIENT_CURRENT_PRICES"
    with clean_db() as session:
        assert session.scalar(select(func.count(Suggestion.id))) == 0


def test_database_failure_is_not_a_supplier_success_and_run_is_closed(
    api_client, clean_db, monkeypatch
):
    from sqlalchemy.exc import OperationalError

    from app.api.procurement_routes import get_price_transport
    from app.main import app
    from app.services.prices import PriceService

    client, _, item, _ = catalog(api_client)

    def fail(*args):
        raise OperationalError("private SQL marker", {}, RuntimeError("private DB marker"))

    monkeypatch.setattr(PriceService, "_record_quote", fail)
    app.dependency_overrides[get_price_transport] = lambda: EvidenceTransport()
    try:
        response = client.post(f"/prices/{item['id']}/refresh")
    finally:
        app.dependency_overrides.pop(get_price_transport, None)
    assert response.status_code == 503 and "private" not in response.text
    with clean_db() as session:
        run = session.scalar(select(ScrapeRun))
        assert run.status == "ERROR" and run.finished_at is not None
        assert session.scalar(select(func.count(PriceQuote.id))) == 0


def test_unexpected_persistence_exception_does_not_leave_an_implicit_transaction(
    api_client, monkeypatch
):
    from app.services.prices import PriceService

    client, _, item, _ = catalog(api_client)
    original = PriceService._record_quote

    def unexpected(service, quote, run, config):
        if config["adapter"] == "central":
            raise TypeError("controlled unexpected boundary failure")
        return original(service, quote, run, config)

    monkeypatch.setattr(PriceService, "_record_quote", unexpected)
    result = refresh(client, item["id"], EvidenceTransport())
    assert sorted(run["status"] for run in result["runs"]) == ["ERROR", "OK", "OK"]
    assert all(run["finished_at"] for run in result["runs"])
