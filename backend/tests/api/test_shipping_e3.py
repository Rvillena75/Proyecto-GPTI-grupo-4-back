"""Regression at the boundary between integrated dispatch work and persisted suggestions."""

from decimal import Decimal

from test_e2_completion import generate, prepared


def test_shipping_is_frozen_and_change_requires_new_decision(api_client):
    client, _, item, products = prepared(api_client)
    for product in products:
        result = client.patch(
            f"/suppliers/{product['supplier_id']}", json={"shipping_cost_clp": "1500"}
        )
        assert result.status_code == 200
    suggestion = generate(client, item["id"])
    assert Decimal(suggestion["shipping_cost_clp"]) == 1500
    assert Decimal(suggestion["total_clp"]) == (
        Decimal(suggestion["subtotal_clp"]) + Decimal(suggestion["shipping_cost_clp"])
    )
    product = next(p for p in products if p["id"] == suggestion["supplier_product_id"])
    client.patch(f"/suppliers/{product['supplier_id']}", json={"shipping_cost_clp": "2500"})
    result = client.post(f"/suggestions/{suggestion['id']}/decision", json={"decision": "APPROVE"})
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "STALE_SHIPPING"
    assert client.get("/orders").json() == []
