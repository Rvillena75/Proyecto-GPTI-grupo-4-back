"""Frozen synthetic Dataset v2 and configured real source catalog, for EMPTY *_test DBs only."""

import argparse
import hashlib
from datetime import UTC, datetime
from pathlib import Path

from app.e2.contracts import PackSpec
from app.e2.dataset import generate, json_text
from app.e2.replay import replay_inventory
from app.persistence.database import session_factory
from app.services.catalog import CatalogService
from scraper.pipeline import load_products


def prepare(fixture_root: Path, factory) -> dict:
    url = factory.kw["bind"].url
    if url.get_backend_name() != "postgresql" or not (url.database or "").endswith("_test"):
        raise ValueError("Demo requires an empty isolated PostgreSQL *_test database")
    products = load_products(fixture_root / "config/products.json")
    data = generate(fixture_root / "config/dataset_v2.json")
    report = replay_inventory(data, factory)
    for source in products:
        with factory() as session:
            service = CatalogService(session)
            supplier = service.create_supplier(
                {
                    "name": source["name"],
                    "price_source_url": source["url"],
                    "lead_time_days": source["lead_time_days"],
                    "adapter": source["adapter"],
                    "is_active": source["is_active"],
                },
                datetime.now(UTC),
            )
            pack = PackSpec.model_validate(source["pack"]).to_pack()
            service.create_product(
                supplier.id,
                {
                    "item_id": report["item_id"],
                    "name_on_site": source["name_on_site"],
                    "url": source["url"],
                    "pack_quantity": pack.pack_quantity,
                    "min_packs": source["min_packs"],
                    "is_active": source["is_active"],
                    "scrape_config": {
                        k: source[k] for k in ("sku", "brand", "expected_name_tokens", "pack")
                    },
                },
            )
    return {
        **report,
        "dataset_sha256": hashlib.sha256(json_text(data).encode()).hexdigest(),
        "catalog": "Dated E2 source definitions; delivery times are synthetic assumptions",
        "prices": "NONE_IMPORTED; acquire fresh public prices separately",
        "users": "NONE_CREATED; use controlled app.manage_users CLI",
        "no_e3_simulation": True,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, required=True)
    args = parser.parse_args(argv)
    result = prepare(args.fixture_root, session_factory())
    print(json_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
