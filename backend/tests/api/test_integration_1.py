"""Endpoints for frontend integration 1: list/edit items, history balances, lead-time alert."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.models import Order, Suggestion, Supplier, SupplierProduct

START = datetime(2026, 9, 1, 3, tzinfo=UTC)  # 1 Sep 00:00 in Santiago


def _item(client: TestClient, name: str = "Azúcar", quantity: str = "30") -> dict:
    response = client.post(
        "/items",
        json={"name": name, "base_unit": "kg", "initial_quantity": quantity, "initial_unit": "kg"},
    )
    assert response.status_code == 201
    return response.json()


def test_list_and_patch_items(api_client: tuple[TestClient, object]) -> None:
    client, _ = api_client
    assert client.get("/items").json() == []
    first = _item(client)
    second = _item(client, "Aceite", "12")
    listed = client.get("/items").json()
    assert [item["id"] for item in listed] == [first["id"], second["id"]]
    assert listed[1]["current_stock"] == "12.000000"

    renamed = client.patch(f"/items/{first['id']}", json={"name": "  Azúcar Iansa  "})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Azúcar Iansa"
    assert renamed.json()["manual_safe_stock"] is None

    with_safe = client.patch(f"/items/{first['id']}", json={"manual_safe_stock": "5"})
    assert with_safe.json()["manual_safe_stock"] == "5.000000"
    assert with_safe.json()["name"] == "Azúcar Iansa"  # omitted field untouched
    restored = client.patch(f"/items/{first['id']}", json={"manual_safe_stock": None})
    assert restored.json()["manual_safe_stock"] is None

    for body, code in (
        ({}, "EMPTY_UPDATE"),
        ({"name": "   "}, "INVALID_NAME"),
        ({"base_unit": "L"}, "INVALID_REQUEST"),
    ):
        response = client.patch(f"/items/{first['id']}", json=body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == code
    missing = client.patch("/items/999", json={"name": "X"})
    assert missing.status_code == 404


def test_history_has_balance_after_in_occurrence_order(
    api_client: tuple[TestClient, object],
) -> None:
    client, set_time = api_client
    set_time(START)
    item = _item(client, quantity="10")
    set_time(START + timedelta(days=2))
    exit_ = client.post(
        "/movements", json={"item_id": item["id"], "kind": "EXIT", "quantity": "4", "unit": "kg"}
    )
    assert exit_.json()["balance_after"] == "6.000000"
    assert exit_.json()["created_by"] is None
    # Recorded later but occurred before the exit: it changes the exit's balance_after too.
    retro = client.post(
        "/movements",
        json={
            "item_id": item["id"],
            "kind": "ENTRY",
            "quantity": "5",
            "unit": "kg",
            "occurred_at": (START + timedelta(days=1)).isoformat(),
        },
    )
    assert retro.json()["balance_after"] == "15.000000"
    reversal = client.post(f"/movements/{exit_.json()['id']}/reverse", json={})
    assert reversal.json()["balance_after"] == "15.000000"

    history = client.get(f"/items/{item['id']}/movements").json()
    assert [m["kind"] for m in history] == ["INITIAL", "EXIT", "ENTRY", "REVERSAL"]
    assert [m["balance_after"] for m in history] == [
        "10.000000",
        "11.000000",
        "15.000000",
        "15.000000",
    ]
    assert client.get(f"/items/{item['id']}").json()["current_stock"] == "15.000000"


def test_alert_uses_longest_lead_time_and_orders_in_transit(
    api_client: tuple[TestClient, object], clean_db: sessionmaker[Session]
) -> None:
    client, set_time = api_client
    set_time(START)
    item = _item(client, quantity="37")
    # 1 kg per day for 28 days -> 7 kg/week, leaving 9 kg (the mockup's numbers).
    for day in range(1, 29):
        set_time(START + timedelta(days=day, hours=12))
        client.post(
            "/movements",
            json={"item_id": item["id"], "kind": "EXIT", "quantity": "1", "unit": "kg"},
        )
    now = START + timedelta(days=29, hours=12)
    set_time(now)
    client.patch(f"/items/{item['id']}", json={"manual_safe_stock": "5"})

    before = client.get(f"/items/{item['id']}").json()["assessment"]
    assert before["lead_time_days"] is None
    assert before["alert_status"] == "OK"  # 9 > 5 without lead time

    with clean_db() as session, session.begin():
        for name, days in (("A", 2), ("B", 4), ("C", 3), ("Inactivo", 9)):
            supplier = Supplier(
                name=name,
                lead_time_days=days,
                is_active=name != "Inactivo",
                created_at=now,
                updated_at=now,
            )
            session.add(supplier)
            session.flush()
            session.add(
                SupplierProduct(
                    supplier_id=supplier.id,
                    item_id=item["id"],
                    name_on_site=f"Azúcar {name}",
                    url=f"https://example.test/{name}",
                    pack_quantity=D(1),
                    min_packs=1,
                    is_active=True,
                )
            )

    state = client.get(f"/items/{item['id']}").json()["assessment"]
    assert state["lead_time_days"] == 4  # longest among active suppliers (C12)
    assert state["projected_stock_at_arrival"] == "5.000000"
    assert state["alert_status"] == "LOW_STOCK"
    assert D(state["suggested_quantity"]) == D(9)
    assert [a["item_id"] for a in client.get("/alerts").json()] == [item["id"]]

    with clean_db() as session, session.begin():
        suggestion = Suggestion(
            item_id=item["id"],
            status="APPROVED",
            required_qty=D(9),
            in_transit_qty=D(0),
            purchased_qty=D(10),
            inputs_snapshot={},
            created_at=now,
        )
        session.add(suggestion)
        session.flush()
        session.add(
            Order(suggestion_id=suggestion.id, code="OC-0001", status="APPROVED", decided_at=now)
        )

    attended = client.get(f"/items/{item['id']}/alert").json()
    assert attended["alert_status"] == "IN_TRANSIT"
    assert attended["in_transit_quantity"] == "10.000000"
    assert client.get("/alerts").json() == []  # attended alerts leave the action list
    assert client.get(f"/items/{item['id']}").json()["assessment"]["suggested_quantity"] == "0"
