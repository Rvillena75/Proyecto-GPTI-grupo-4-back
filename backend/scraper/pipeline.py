"""Daily/on-demand worker for three legitimate public product-page adapters."""

import argparse
import hashlib
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.domain.commercial import CommercialPack
from app.domain.units import amount
from app.e2.contracts import PackSpec
from app.e2.dataset import json_text
from scraper.adaptadores import ADAPTERS
from scraper.contracts import ScrapeError
from scraper.normalize import normalize, validate_public_product_url
from scraper.store import QuoteStore, quote_state, save, worker_lock
from scraper.transport import PublicTransport, robots_allowed

LOG = logging.getLogger("stocksmart.prices")


def load_products(path: Path) -> list[dict]:
    config = json.loads(path.read_text(encoding="utf-8"))
    suppliers = config["suppliers"]
    if {s["adapter"] for s in suppliers} != set(ADAPTERS) or len(suppliers) != 3:
        raise ValueError(
            "E2 requires exactly Central Mayorista, Jumbo and Santa Isabel; Alvi is excluded"
        )
    if (
        len({s["supplier_id"] for s in suppliers}) != 3
        or len({s["supplier_product_id"] for s in suppliers}) != 3
    ):
        raise ValueError("Duplicate supplier/product IDs")
    for supplier in suppliers:
        validate_public_product_url(supplier["adapter"], supplier["url"])
        PackSpec.model_validate(supplier["pack"])
        amount(Decimal(supplier["shipping_cost_clp"]), allow_zero=True)
        if type(supplier["min_packs"]) is not int or supplier["min_packs"] < 1:
            raise ValueError("Positive minimum packs required")
        supplier["item_id"] = config["item_id"]
    return suppliers


def compare_offers(quotes: list[dict], suppliers: list[dict], required: Decimal) -> dict:
    required = amount(required, allow_zero=True)
    by_product = {
        supplier["supplier_product_id"]: supplier for supplier in suppliers if supplier["is_active"]
    }
    options = []
    for quote in quotes:
        supplier = by_product.get(quote["supplier_product_id"])
        if supplier is None:
            continue
        pack: CommercialPack = PackSpec.model_validate(supplier["pack"]).to_pack()
        count = pack.packs_for(required, supplier["min_packs"])
        shipping = Decimal(supplier["shipping_cost_clp"]) if count else Decimal(0)
        total = count * Decimal(quote["pack_price_clp"]) + shipping
        options.append(
            {
                "supplier_id": supplier["supplier_id"],
                "supplier_product_id": supplier["supplier_product_id"],
                "supplier": supplier["name"],
                "quote_id": quote["id"],
                "packs": count,
                "purchased_quantity": str(pack.pack_quantity * count),
                "base_unit": pack.base_unit,
                "total_clp": str(total),
                "shipping_cost_clp": str(shipping),
                "observed_at": quote["observed_at"],
            }
        )
    options.sort(key=lambda row: (Decimal(row["total_clp"]), row["supplier_id"]))
    if len({row["supplier_id"] for row in options}) < 2:
        return {"status": "INSUFFICIENT_CURRENT_PRICES", "options": options, "recommended": None}
    if required == 0:
        return {"status": "NO_PURCHASE_REQUIRED", "options": options, "recommended": None}
    # Multiple formats from one supplier are not two independent comparable suppliers.
    best = options[0]
    second = next(row for row in options if row["supplier_id"] != best["supplier_id"])
    savings = Decimal(second["total_clp"]) - Decimal(best["total_clp"])
    return {
        "status": "COMPARABLE_WITH_CONTEXT_ASSUMPTIONS",
        "options": options,
        "recommended": best,
        "next_best_total_clp": second["total_clp"],
        "savings_clp": str(savings),
        "potential_savings_percent": str(100 * savings / Decimal(second["total_clp"])),
        "commercial_feasibility": "NOT_VERIFIED_BASKET_MINIMUM_LOCATION_LOGISTICS",
        "operating_parameters_provenance": "SYNTHETIC_ASSUMPTION",
    }


def run_pipeline(
    config_path: Path,
    output: Path,
    *,
    mode: str = "live",
    evidence: Path | None = None,
    reference: datetime | None = None,
    required: Decimal = Decimal(100),
    transport=None,
) -> dict:
    if mode not in {"live", "replay"} or (mode == "replay" and evidence is None):
        raise ValueError("Use live or replay with the original evidence directory")
    if reference is not None and (reference.tzinfo is None or reference.utcoffset() is None):
        raise ValueError("Reference time must include timezone")
    suppliers = load_products(config_path)
    run_id = uuid4().hex
    started = datetime.now(UTC)
    store = QuoteStore(output)
    owned_transport = transport is None and mode == "live"
    run = {
        "id": run_id,
        "started_at": started.isoformat(),
        "execution_mode": mode,
        "suppliers": [],
        "status": "ERROR",
    }
    with worker_lock(output):
        try:
            if owned_transport:
                transport = PublicTransport(
                    output / "evidence" / run_id, pacing_path=output / ".request_times.json"
                )
            for supplier in suppliers:
                result = {
                    "supplier_id": supplier["supplier_id"],
                    "supplier": supplier["name"],
                    "supplier_product_id": supplier["supplier_product_id"],
                    "url": supplier["url"],
                }
                if not supplier["is_active"]:
                    result["status"] = "INACTIVE"
                    run["suppliers"].append(result)
                    continue
                try:
                    adapter = ADAPTERS[supplier["adapter"]](supplier, transport)
                    if mode == "replay":
                        stem = evidence / supplier["evidence_stem"]
                        metadata = json.loads(
                            stem.with_suffix(".json").read_text(encoding="utf-8-sig")
                        )
                        body_bytes = stem.with_suffix(".html").read_bytes()
                        digest = hashlib.sha256(body_bytes).hexdigest()
                        if (
                            metadata["url"] != supplier["url"]
                            or metadata["status"] != 200
                            or digest != metadata.get("artifact_sha256", metadata["sha256"])
                        ):
                            raise ScrapeError(
                                "EVIDENCE_MISMATCH", "Original public response/hash is invalid"
                            )
                        robots_name = {
                            "central": "central_robots",
                            "jumbo": "jumbo_robots",
                            "santa_isabel": "santa_isabel_robots",
                        }[supplier["adapter"]]
                        if not robots_allowed(
                            (evidence / f"{robots_name}.txt").read_text(encoding="utf-8"),
                            supplier["url"],
                        ):
                            raise ScrapeError(
                                "ROBOTS_DENIED", "Recorded robots policy prohibits this page"
                            )
                        raw = adapter.parse(body_bytes.decode("utf-8"), supplier["url"])
                        observed = datetime.fromisoformat(metadata["observed_at"])
                        # Verify artifact bytes and retain the original acquisition/quote hash.
                        # sha256 identifies the raw HTTP response kept in the private backup.
                        digest = metadata["sha256"]
                    else:
                        raw = adapter.fetch(supplier["url"])
                        response = transport.responses[supplier["url"]]
                        observed, digest = response.observed_at, response.sha256
                    if not raw.available and raw.price_text is None:
                        result["observed_at"] = observed.isoformat()
                        raise ScrapeError(
                            "OUT_OF_STOCK", "Product explicitly out of stock with no price"
                        )
                    quote = normalize(raw, supplier, observed, digest)
                    quote["run_id"] = run_id
                    store.add_quote(quote)
                    result.update(
                        status="OK" if raw.available else "OUT_OF_STOCK",
                        quote_id=quote["id"],
                        observed_at=quote["observed_at"],
                    )
                    if not raw.available:
                        LOG.warning("supplier=%s status=OUT_OF_STOCK", supplier["name"])
                except Exception as exc:
                    code = getattr(exc, "code", "UNEXPECTED_ERROR")
                    result.update(
                        status="OUT_OF_STOCK" if code == "OUT_OF_STOCK" else "ERROR",
                        error_code=code,
                        error=str(exc),
                    )
                    LOG.exception(
                        "supplier=%s code=%s url=%s",
                        supplier["name"],
                        result["error_code"],
                        supplier["url"],
                    )
                run["suppliers"].append(result)
            completed = sum(row["status"] in {"OK", "OUT_OF_STOCK"} for row in run["suppliers"])
            active_count = sum(supplier["is_active"] for supplier in suppliers)
            run["status"] = (
                "OK"
                if completed == active_count and completed
                else ("PARTIAL" if completed else "ERROR")
            )
            at = reference or datetime.now(UTC)
            run["reference_at"] = at.isoformat()
            run["finished_at"] = datetime.now(UTC).isoformat()
            current = []
            for supplier, result in zip(suppliers, run["suppliers"], strict=True):
                quote = store.latest(supplier["supplier_product_id"])
                state = quote_state(quote, at)
                if result["status"] == "INACTIVE":
                    state = "INACTIVE"
                if result["status"] == "OUT_OF_STOCK":
                    state = "OUT_OF_STOCK"
                if (
                    result["status"] == "ERROR"
                    and store.last_known_availability(supplier["supplier_product_id"]) is False
                ):
                    state = "OUT_OF_STOCK"
                if quote and (
                    quote["sku"] != supplier["sku"]
                    or Decimal(quote["pack_quantity"])
                    != PackSpec.model_validate(supplier["pack"]).to_pack().pack_quantity
                ):
                    state = "CONFIGURATION_CHANGED"
                result["quote_state"] = state
                result["using_previous_current_quote"] = (
                    result["status"] == "ERROR" and state == "CURRENT"
                )
                if quote and state == "CURRENT":
                    current.append(quote)
            run["quotes"] = current
            run["comparison"] = compare_offers(current, suppliers, required)
            store.add_run(run)
            save(output / "latest.json", run)
        finally:
            if owned_transport and transport is not None:
                transport.close()
    return run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--mode", choices=["live", "replay"], default="live")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--at", type=datetime.fromisoformat)
    parser.add_argument("--required-quantity", type=Decimal, default=Decimal(100))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(args.out / "pipeline.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    result = run_pipeline(
        args.config,
        args.out,
        mode=args.mode,
        evidence=args.evidence,
        reference=args.at,
        required=args.required_quantity,
    )
    print(
        json_text(
            {
                "status": result["status"],
                "suppliers": result["suppliers"],
                "comparison": result["comparison"],
            }
        )
    )
    raise SystemExit(0 if result["status"] == "OK" else (2 if result["status"] == "PARTIAL" else 1))


if __name__ == "__main__":
    main()
