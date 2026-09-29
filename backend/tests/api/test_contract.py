from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from fastapi.testclient import TestClient


def test_health_openapi_and_scope(api_client: tuple[TestClient, object]) -> None:
    client, _ = api_client
    assert client.get("/health").json() == {"status": "ok"}
    spec = client.get("/openapi.json").json()
    assert "/items" in spec["paths"]
    assert "/movements" in spec["paths"]
    assert "/counts" in spec["paths"]
    assert "/alerts" in spec["paths"]
    assert not any("price" in route or "supplier" in route for route in spec["paths"])
    assert "delete" not in spec["paths"]["/items/{item_id}/movements"]
    movement_errors = spec["paths"]["/movements"]["post"]["responses"]
    for status in ("404", "409", "422"):
        assert movement_errors[status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }
    error_schema = spec["components"]["schemas"]["ErrorResponse"]
    assert error_schema["properties"]["error"]["$ref"] == "#/components/schemas/ErrorDetail"


def test_item_validation_and_initial_stock_not_backdated(
    api_client: tuple[TestClient, object],
) -> None:
    client, _ = api_client
    base = {"name": "Azúcar", "base_unit": "kg", "initial_quantity": "10", "initial_unit": "kg"}
    invalid = client.post("/items", json={**base, "base_unit": "g"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_REQUEST"
    retro = client.post("/items", json={**base, "occurred_at": "2026-09-01T00:00:00Z"})
    assert retro.status_code == 422
    created = client.post("/items", json=base)
    assert created.status_code == 201
    data = created.json()
    assert data["observation_started_at"] is not None
    assert data["current_stock"] == "10.000000"
    other = client.post("/items", json={**base, "name": "Harina", "initial_quantity": "20"})
    assert other.status_code == 201
    assert other.json()["current_stock"] == "20.000000"
    duplicate = client.post(
        f"/items/{data['id']}/initial-stock", json={"quantity": "1", "unit": "kg"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "INITIAL_STOCK_ALREADY_EXISTS"
    naive_time = client.post(
        "/movements",
        json={
            "item_id": data["id"],
            "kind": "ENTRY",
            "quantity": "1",
            "unit": "kg",
            "occurred_at": "2026-09-28T12:00:00",
        },
    )
    assert naive_time.status_code == 422
    assert naive_time.json()["error"]["code"] == "INVALID_TIMESTAMP"


def test_invalid_numeric_range_and_malformed_timezone_are_4xx(
    api_client: tuple[TestClient, object],
) -> None:
    client, _ = api_client
    base = {"name": "Azúcar", "base_unit": "kg", "initial_quantity": "10", "initial_unit": "kg"}
    too_large = client.post("/items", json={**base, "initial_quantity": "1000000000000000000"})
    assert too_large.status_code == 422
    assert too_large.json()["error"]["code"] == "AMOUNT_OUT_OF_RANGE"
    malformed_zone = client.post("/items", json={**base, "timezone": "../Etc/UTC"})
    assert malformed_zone.status_code == 422
    assert malformed_zone.json()["error"]["code"] == "INVALID_TIMEZONE"


def test_vertical_inventory_forecast_alert_count(api_client: tuple[TestClient, object]) -> None:
    client, set_time = api_client
    first = datetime(2026, 9, 1, 3, tzinfo=UTC)  # 31 Aug 23:00 local
    set_time(first)
    created = client.post(
        "/items",
        json={"name": "Azúcar", "base_unit": "kg", "initial_quantity": "66", "initial_unit": "kg"},
    )
    assert created.status_code == 201
    item_id = created.json()["id"]
    assert created.json()["assessment"]["alert_status"] == "INSUFFICIENT_HISTORY"
    assert created.json()["assessment"]["suggested_quantity"] is None

    set_time(datetime(2026, 9, 5, 15, tzinfo=UTC))
    exit_ = client.post(
        "/movements",
        json={"item_id": item_id, "kind": "EXIT", "quantity": "56", "unit": "kg"},
    )
    assert exit_.status_code == 201
    assert exit_.json()["delta"] == "-56.000000"
    assert client.get(f"/items/{item_id}").json()["current_stock"] == "10.000000"

    set_time(datetime(2026, 9, 29, 15, tzinfo=UTC))
    state = client.get(f"/items/{item_id}")
    assert state.status_code == 200
    data = state.json()
    assert data["consumption"]["complete_days"] == 28
    assert data["consumption"]["weekly_average"] == "14.000000"
    assert data["assessment"]["daily_average"] == "2.000000"
    assert D(data["assessment"]["remaining_days"]) == D(5)
    assert data["assessment"]["alert_status"] == "LOW_STOCK"
    assert data["assessment"]["safe_stock"] == "14.000000"
    assert data["assessment"]["suggested_quantity"] == "18.000000"
    assert client.get(f"/items/{item_id}/alert").json()["is_low_stock"] is True
    assert any(alert["item_id"] == item_id for alert in client.get("/alerts").json())

    count = client.post(
        "/counts",
        json={"item_id": item_id, "physical_quantity": "7", "unit": "kg", "cause": "otra"},
    )
    assert count.status_code == 201
    assert count.json()["theoretical_before"] == "10.000000"
    assert count.json()["difference"] == "-3.000000"
    assert count.json()["shortage"] == "3.000000"
    after = client.get(f"/items/{item_id}").json()
    assert after["current_stock"] == "7.000000"
    assert after["consumption"]["weekly_average"] == "14.000000"
    assert after["assessment"]["suggested_quantity"] == "21.000000"
    assert len(client.get(f"/items/{item_id}/counts").json()) == 1
    assert len(client.get(f"/items/{item_id}/movements").json()) == 3


def test_api_reports_unrepresentable_positive_consumption_projection(
    api_client: tuple[TestClient, object],
) -> None:
    client, set_time = api_client
    start = datetime(2026, 9, 1, tzinfo=UTC)
    set_time(start)
    item_id = client.post(
        "/items",
        json={
            "name": "Consumo mínimo",
            "base_unit": "kg",
            "initial_quantity": "1000",
            "initial_unit": "kg",
            "timezone": "UTC",
        },
    ).json()["id"]
    set_time(start + timedelta(days=2))
    assert (
        client.post(
            "/movements",
            json={"item_id": item_id, "kind": "EXIT", "quantity": "0.000001", "unit": "kg"},
        ).status_code
        == 201
    )
    set_time(start + timedelta(days=28, hours=12))
    response = client.get(f"/items/{item_id}")
    assert response.status_code == 200
    body = response.json()
    assert D(body["consumption"]["weekly_average"]) > 0
    assert D(body["assessment"]["daily_average"]) > 0
    assert D(body["assessment"]["remaining_days"]) > 1_000_000_000
    assert body["assessment"]["depletion_status"] == "OUT_OF_RANGE"
    assert body["assessment"]["depletion_at"] is None


def test_manual_threshold_and_api_error_contract(api_client: tuple[TestClient, object]) -> None:
    client, set_time = api_client
    created = client.post(
        "/items",
        json={"name": "Leche", "base_unit": "L", "initial_quantity": "7", "initial_unit": "L"},
    ).json()
    item_id = created["id"]
    manual = client.put(f"/items/{item_id}/safe-stock", json={"manual_safe_stock": "10"})
    assert manual.status_code == 200
    assert manual.json()["assessment"]["alert_status"] == "LOW_STOCK"
    assert manual.json()["assessment"]["alert_source"] == "MANUAL_THRESHOLD"
    assert manual.json()["assessment"]["suggested_quantity"] is None
    removed = client.put(f"/items/{item_id}/safe-stock", json={"manual_safe_stock": None})
    assert removed.json()["assessment"]["alert_status"] == "INSUFFICIENT_HISTORY"
    invalid = client.post(
        "/movements",
        json={"item_id": item_id, "kind": "EXIT", "quantity": "8", "unit": "L"},
    )
    assert invalid.status_code == 409
    assert invalid.json()["error"]["code"] == "NEGATIVE_STOCK"
    assert client.get(f"/items/{item_id}").json()["current_stock"] == "7.000000"
    assert client.get("/items/999999").status_code == 404


def test_manual_override_stays_fixed_while_automatic_rate_changes(
    api_client: tuple[TestClient, object],
) -> None:
    client, set_time = api_client
    start = datetime(2026, 9, 1, 12, tzinfo=UTC)
    set_time(start)
    item_id = client.post(
        "/items",
        json={"name": "Aceite", "base_unit": "L", "initial_quantity": "30", "initial_unit": "L"},
    ).json()["id"]
    client.put(f"/items/{item_id}/safe-stock", json={"manual_safe_stock": "10"})
    set_time(start + timedelta(days=1))
    client.post(
        "/movements",
        json={"item_id": item_id, "kind": "EXIT", "quantity": "14", "unit": "L"},
    )
    set_time(start + timedelta(days=2))
    state = client.get(f"/items/{item_id}").json()
    assert D(state["consumption"]["weekly_average"]) == D(98)
    assert D(state["assessment"]["safe_stock"]) == D(10)
    assert state["assessment"]["alert_status"] == "OK"
    set_time(start + timedelta(days=3))
    state = client.get(f"/items/{item_id}").json()
    assert D(state["assessment"]["safe_stock"]) == D(10)
    automatic = client.put(f"/items/{item_id}/safe-stock", json={"manual_safe_stock": None})
    assert D(automatic.json()["assessment"]["safe_stock"]) == D(49)
    assert automatic.json()["assessment"]["alert_status"] == "LOW_STOCK"


def test_api_retro_count_checkpoint_and_current_correction(
    api_client: tuple[TestClient, object],
) -> None:
    client, set_time = api_client
    start = datetime(2026, 9, 1, 12, tzinfo=UTC)
    set_time(start)
    item_id = client.post(
        "/items",
        json={"name": "Harina", "base_unit": "kg", "initial_quantity": "10", "initial_unit": "kg"},
    ).json()["id"]
    set_time(start + timedelta(days=1))
    wrong = client.post(
        "/movements",
        json={"item_id": item_id, "kind": "EXIT", "quantity": "5", "unit": "kg"},
    ).json()
    set_time(start + timedelta(days=2))
    count = client.post(
        "/counts", json={"item_id": item_id, "physical_quantity": "10", "unit": "kg"}
    ).json()
    set_time(start + timedelta(days=3))
    retro = client.post(
        "/movements",
        json={
            "item_id": item_id,
            "kind": "ENTRY",
            "quantity": "1",
            "unit": "kg",
            "occurred_at": (start + timedelta(hours=1)).isoformat(),
        },
    )
    assert retro.status_code == 409
    assert retro.json()["error"]["code"] == "HISTORICAL_RECONCILIATION_CONFLICT"
    reverse = client.post(f"/movements/{wrong['id']}/reverse", json={})
    assert reverse.status_code == 409
    correction = client.post(
        f"/movements/{wrong['id']}/corrections", json={"note": "Duplicada; conteo la absorbió"}
    )
    assert correction.status_code == 201
    assert D(correction.json()["delta"]) == 0
    assert client.get(f"/items/{item_id}").json()["current_stock"] == "10.000000"
    persisted = client.get(f"/items/{item_id}/counts").json()[0]
    for field in ("id", "item_id", "adjustment_movement_id", "cause"):
        assert persisted[field] == count[field]
    for field in ("theoretical_before", "physical_stock", "difference", "shortage"):
        assert D(persisted[field]) == D(count[field])
    for field in ("occurred_at", "created_at"):
        assert datetime.fromisoformat(persisted[field]) == datetime.fromisoformat(count[field])


def test_api_present_correction_has_signed_stock_effect(
    api_client: tuple[TestClient, object],
) -> None:
    client, set_time = api_client
    start = datetime(2026, 9, 1, 12, tzinfo=UTC)
    set_time(start)
    item_id = client.post(
        "/items",
        json={"name": "Harina", "base_unit": "kg", "initial_quantity": "10", "initial_unit": "kg"},
    ).json()["id"]
    set_time(start + timedelta(days=1))
    wrong = client.post(
        "/movements",
        json={"item_id": item_id, "kind": "EXIT", "quantity": "5", "unit": "kg"},
    ).json()
    set_time(start + timedelta(days=2))
    count = client.post(
        "/counts", json={"item_id": item_id, "physical_quantity": "10", "unit": "kg"}
    ).json()
    set_time(start + timedelta(days=3))
    correction = client.post(
        f"/movements/{wrong['id']}/corrections",
        json={"note": "Ajuste actual documentado", "stock_effect": "2"},
    )
    assert correction.status_code == 201
    assert correction.json()["delta"] == "2.000000"
    assert correction.json()["invalidates_movement_id"] == wrong["id"]
    assert client.get(f"/items/{item_id}").json()["current_stock"] == "12.000000"
    persisted = client.get(f"/items/{item_id}/counts").json()[0]
    for field in ("id", "item_id", "adjustment_movement_id", "corrects_movement_id", "cause"):
        assert persisted[field] == count[field]
    for field in ("theoretical_before", "physical_stock", "difference", "shortage"):
        assert D(persisted[field]) == D(count[field])
    for field in ("occurred_at", "created_at"):
        assert datetime.fromisoformat(persisted[field]) == datetime.fromisoformat(count[field])


def test_api_shortage_rate_and_zero_denominator(api_client: tuple[TestClient, object]) -> None:
    client, set_time = api_client
    start = datetime(2026, 9, 1, 12, tzinfo=UTC)
    set_time(start)
    item_id = client.post(
        "/items",
        json={"name": "Azúcar", "base_unit": "kg", "initial_quantity": "20", "initial_unit": "kg"},
    ).json()["id"]
    set_time(start + timedelta(hours=1))
    client.post(
        "/movements",
        json={"item_id": item_id, "kind": "ENTRY", "quantity": "10", "unit": "kg"},
    )
    set_time(start + timedelta(hours=2))
    client.post("/counts", json={"item_id": item_id, "physical_quantity": "32", "unit": "kg"})
    set_time(start + timedelta(hours=3))
    client.post("/counts", json={"item_id": item_id, "physical_quantity": "29", "unit": "kg"})
    response = client.get(
        f"/items/{item_id}/shortage-rate",
        params={"start": start.isoformat(), "end": (start + timedelta(days=1)).isoformat()},
    )
    assert response.status_code == 200
    assert D(response.json()["rate_percent"]) == D("9.375")
    zero_id = client.post(
        "/items",
        json={"name": "Sal", "base_unit": "kg", "initial_quantity": "0", "initial_unit": "kg"},
    ).json()["id"]
    zero = client.get(
        f"/items/{zero_id}/shortage-rate",
        params={"start": start.isoformat(), "end": (start + timedelta(days=1)).isoformat()},
    )
    assert zero.json()["status"] == "NOT_CALCULABLE"
    assert zero.json()["rate_percent"] is None
