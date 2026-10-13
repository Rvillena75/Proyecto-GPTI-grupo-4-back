"""Independent whole-pack costs with multiple formats of the same supplier."""

from decimal import Decimal as D

from scraper.pipeline import compare_offers


def test_next_best_option_is_a_different_supplier_and_uses_each_products_format():
    suppliers = []
    quotes = []
    for supplier, product, quantity, price in [
        (1, 11, "1", "200"),
        (1, 12, "10", "1000"),
        (2, 21, "5", "600"),
    ]:
        suppliers.append(
            {
                "supplier_id": supplier,
                "supplier_product_id": product,
                "name": str(supplier),
                "is_active": True,
                "min_packs": 1,
                "shipping_cost_clp": "0",
                "pack": {
                    "commercial_format": "caja",
                    "content_quantity": quantity,
                    "content_unit": "kg",
                    "units_per_pack": 1,
                    "base_unit": "kg",
                },
            }
        )
        quotes.append(
            {
                "id": product,
                "supplier_id": supplier,
                "supplier_product_id": product,
                "pack_price_clp": price,
                "observed_at": "2026-10-08T12:00:00Z",
            }
        )
    result = compare_offers(quotes, suppliers, D(8))
    assert result["recommended"]["supplier_product_id"] == 12
    assert result["recommended"]["packs"] == 1
    assert D(result["recommended"]["total_clp"]) == 1000
    assert D(result["next_best_total_clp"]) == 1200
    assert D(result["savings_clp"]) == 200
    only_a = compare_offers(quotes[:2], suppliers, D(8))
    assert only_a["status"] == "INSUFFICIENT_CURRENT_PRICES"
    assert only_a["recommended"] is None
