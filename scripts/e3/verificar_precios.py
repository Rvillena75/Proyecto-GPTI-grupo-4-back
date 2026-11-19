"""Run the real public price worker against an empty local, isolated PostgreSQL database."""

import argparse
import json
import os
from pathlib import Path

from app.e2.dataset import json_text
from app.persistence.models import PriceQuote, ScrapeRun
from app.services.inventory import InventoryService
from local_check_db import local_database
from scraper.pipeline import load_products
from scraper.postgres import run_worker
from simular_protocolo import OPENING, START, ZONE, at, build_inputs, sha, store_catalog
from sqlalchemy import select


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("dataset", "parametros", "protocolo", "output"):
        parser.add_argument("--" + key, required=True, type=Path)
    parser.add_argument("--run", required=True)
    args = parser.parse_args()
    frozen = json.loads(args.dataset.read_text(encoding="utf-8"))
    raw = load_products(args.parametros)
    inputs = build_inputs(frozen, raw, sha(args.protocolo), sha(args.dataset))
    for row in inputs["proveedores"]:
        source = next(s for s in raw if s["supplier_id"] == row["key"])
        row["scrape_config"]["pack"] = source["pack"]
    name = f"stocksmart_e3_{args.run}_precios_test"
    factory = local_database(name)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    os.environ["STOCKSMART_PRICE_EVIDENCE_DIR"] = str(args.output.parent / "transient-prices")
    with factory() as session:
        item = InventoryService(session).create_item(
            "Pechuga asada PR-07", "kg", OPENING, "kg", ZONE.key, None, at(START)
        )
    store_catalog(factory, item.id, inputs["proveedores"])
    code, result = run_worker(only_if_due=True, timezone_name=ZONE.key, emit_stdout=False)
    with factory() as session:
        quotes = session.scalars(select(PriceQuote).order_by(PriceQuote.id)).all()
        runs = session.scalars(select(ScrapeRun).order_by(ScrapeRun.id)).all()
        evidence = {
            "database": name,
            "exit_code": code,
            "status": result["status"],
            "runs": [
                {
                    "id": r.id,
                    "supplier_id": r.supplier_id,
                    "status": r.status,
                    "finished_at": r.finished_at,
                    "detail": json.loads(r.message or "{}"),
                }
                for r in runs
            ],
            "quotes": [
                {
                    "id": q.id,
                    "run_id": q.run_id,
                    "price_clp": q.pack_price_clp,
                    "unit_price_clp": q.unit_price_clp,
                    "observed_at": q.observed_at,
                    "valid_until": q.valid_until,
                    "evidence_sha256": q.evidence_sha256,
                }
                for q in quotes
            ],
        }
    if code == 0:
        repeat_code, repeat = run_worker(
            only_if_due=True, timezone_name=ZONE.key, emit_stdout=False
        )
        assert repeat_code == 0 and repeat["reason"] == "ALREADY_COMPLETED_TODAY"
        with factory() as session:
            assert len(session.scalars(select(PriceQuote.id)).all()) == len(quotes)
            assert len(session.scalars(select(ScrapeRun.id)).all()) == len(runs)
        evidence["reintento"] = repeat
    args.output.write_text(json_text(evidence), encoding="utf-8")
    print(
        json_text(
            {
                "status": result["status"],
                "exit_code": code,
                "quotes": len(quotes),
                "runs": len(runs),
            }
        )
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
