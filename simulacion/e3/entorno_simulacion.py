"""Bootstrap and check a LOCAL, explicitly isolated StockSmart E3 data environment."""

import hashlib
import json
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
BACKEND = PROJECT / "backend"
DATASET = HERE / "dataset_v2" / "dataset.json"
PARAMS = HERE / "parametros_productos.json"
DBURL = os.environ.get("STOCKSMART_SIMULATION_DATABASE_URL", "")

if not DBURL:
    raise SystemExit(
        "Define STOCKSMART_SIMULATION_DATABASE_URL para PostgreSQL local y una base nueva *_test"
    )
parsed = make_url(DBURL)
if (
    parsed.drivername != "postgresql+psycopg"
    or parsed.host not in ("127.0.0.1", "localhost", "::1")
    or not (parsed.database or "").endswith("_test")
    or parsed.query
):
    raise SystemExit(
        "Se exige PostgreSQL local, driver psycopg, base *_test y URL sin parámetros adicionales"
    )
DATABASE = parsed.database
os.environ["STOCKSMART_DATABASE_URL"] = DBURL
os.environ["STOCKSMART_DAILY_SCRAPE_ENABLED"] = "false"
sys.path.insert(0, str(BACKEND))

# Backend modules are imported after selecting the isolated database and source checkout.
from app.e2.contracts import PackSpec  # noqa: E402
from app.e2.replay import replay_inventory  # noqa: E402
from app.persistence.models import PriceQuote, Supplier, SupplierProduct  # noqa: E402
from app.services.catalog import CatalogService  # noqa: E402
from app.services.prices import PriceService  # noqa: E402
from scraper.pipeline import compare_offers, load_products  # noqa: E402

REFERENCE = datetime.fromisoformat("2026-10-08T12:00:00+00:00")
COSTS = {
    "Central Mayorista": Decimal("3990"),
    "Jumbo": Decimal("4990"),
    "Santa Isabel": Decimal("3990"),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def factory():
    return sessionmaker(bind=create_engine(DBURL), expire_on_commit=False)


def create():
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    products = load_products(PARAMS)
    if {p["name"]: Decimal(p["shipping_cost_clp"]) for p in products} != COSTS:
        raise ValueError("Los supuestos no coinciden con los valores acordados")
    manifest = json.loads((DATASET.parent / "manifest.json").read_text(encoding="utf-8"))
    if any(sha(DATASET.parent / name) != digest for name, digest in manifest["files"].items()):
        raise ValueError("El dataset congelado no coincide con su manifiesto")
    admin_url = (
        parsed.set(database="postgres")
        .render_as_string(hide_password=False)
        .replace("postgresql+psycopg://", "postgresql://", 1)
    )
    with psycopg.connect(admin_url, autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (DATABASE,)).fetchone():
            raise RuntimeError("La base ya existe: se conserva sin modificar. Usa verificar.")
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DATABASE)))
    print("Creada base local aislada", DATABASE, flush=True)
    os.chdir(BACKEND)
    command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")
    f = factory()
    report = replay_inventory(data, f)
    ids = {}
    for src in products:
        with f() as session:
            service = CatalogService(session)
            supplier = service.create_supplier(
                {
                    "name": src["name"],
                    "price_source_url": src["url"],
                    "lead_time_days": src["lead_time_days"],
                    "shipping_cost_clp": Decimal(src["shipping_cost_clp"]),
                    "adapter": src["adapter"],
                    "is_active": src["is_active"],
                },
                REFERENCE,
            )
            pack = PackSpec.model_validate(src["pack"]).to_pack()
            product = service.create_product(
                supplier.id,
                {
                    "item_id": report["item_id"],
                    "name_on_site": src["name_on_site"],
                    "url": src["url"],
                    "pack_quantity": pack.pack_quantity,
                    "min_packs": src["min_packs"],
                    "is_active": src["is_active"],
                    "scrape_config": {
                        k: src[k] for k in ("sku", "brand", "expected_name_tokens", "pack")
                    },
                },
            )
            ids[src["supplier_product_id"]] = product.id
    with f() as session, session.begin():
        for q in data["price_quotes"]:
            session.add(
                PriceQuote(
                    supplier_product_id=ids[q["supplier_product_id"]],
                    pack_price_clp=Decimal(q["pack_price_clp"]),
                    unit_price_clp=Decimal(q["unit_price_clp"]),
                    available=q["available"],
                    observed_at=datetime.fromisoformat(q["observed_at"]),
                    valid_until=datetime.fromisoformat(q["valid_until"]),
                    evidence_snapshot={
                        "source": "Dataset v2 congelado; fecha real; no es precio vigente",
                        "source_quote_id": q["id"],
                    },
                    evidence_sha256=q["evidence_sha256"],
                )
            )
    print(
        "Dataset reproducido:",
        report["movements_replayed"],
        "movimientos y",
        report["receipt_entries"],
        "entradas",
        flush=True,
    )
    verify()


def verify():
    f = factory()
    with f() as session:
        suppliers = list(session.scalars(select(Supplier).order_by(Supplier.id)))
        values = {s.name: s.shipping_cost_clp for s in suppliers}
        if values != COSTS:
            raise AssertionError(f"Despachos inesperados: {values}")
        products = list(session.scalars(select(SupplierProduct).order_by(SupplierProduct.id)))
        if len(products) != 3 or len({p.item_id for p in products}) != 1:
            raise AssertionError("Catálogo incompleto")
        item = products[0].item_id
        result = PriceService(session).catalog(item, REFERENCE)
        comp = result["comparison"]
        if comp["status"] != "COMPARABLE_WITH_CONTEXT_ASSUMPTIONS":
            raise AssertionError(f"Comparación no disponible: {comp}")
        rows = comp["options"]
        if {r["supplier"]: Decimal(r["shipping_cost_clp"]) for r in rows} != COSTS:
            raise AssertionError("Costos no aplicados")
        for r in rows:
            assert Decimal(r["total_clp"]) == Decimal(r["subtotal_clp"]) + COSTS[r["supplier"]]
        # Independently compare the same backend inputs with zero shipping, without changing the DB.
        conf = []
        quotes = []
        for p in products:
            s = next(x for x in suppliers if x.id == p.supplier_id)
            q = session.scalar(
                select(PriceQuote)
                .where(PriceQuote.supplier_product_id == p.id)
                .order_by(PriceQuote.id.desc())
            )
            conf.append(
                {
                    "supplier_id": s.id,
                    "supplier_product_id": p.id,
                    "name": s.name,
                    "is_active": True,
                    "min_packs": p.min_packs,
                    "shipping_cost_clp": "0",
                    "pack": {
                        "commercial_format": p.name_on_site,
                        "content_quantity": str(p.pack_quantity),
                        "content_unit": "kg",
                        "units_per_pack": 1,
                        "base_unit": "kg",
                    },
                }
            )
            quotes.append(
                {
                    "id": q.id,
                    "supplier_id": s.id,
                    "supplier_product_id": p.id,
                    "pack_price_clp": str(q.pack_price_clp),
                    "observed_at": q.observed_at.isoformat(),
                }
            )
        zero = compare_offers(quotes, conf, Decimal(str(result["required_quantity"])))
        zero_by = {r["supplier"]: r for r in zero["options"]}
        for r in rows:
            assert (
                Decimal(r["total_clp"]) - Decimal(zero_by[r["supplier"]]["total_clp"])
                == COSTS[r["supplier"]]
            )
        evidence = {
            "database": DATABASE,
            "host": parsed.host,
            "port": parsed.port or 5432,
            "scope": "Base local aislada; no es simulación E3 comparativa completa",
            "dataset_sha256": sha(DATASET),
            "parametros_sha256": sha(PARAMS),
            "fecha_precios": "2026-10-07; precios históricos, no vigentes",
            "comparacion_as_of": REFERENCE.isoformat(),
            "cantidad_kg": str(result["required_quantity"]),
            "opciones": rows,
            "costos_clp": {k: str(v) for k, v in COSTS.items()},
            "comparacion_costo_cero": zero["options"],
            "comprobaciones": [
                "costos leídos desde PostgreSQL",
                "PriceService.catalog y compare_offers",
                "total = subtotal + despacho una sola vez",
                "diferencia frente a despacho cero es el costo indicado por proveedor",
            ],
            "advertencia": (
                "Los montos son supuestos. Central es proxy logístico, no tarifa oficial. "
                "No se validaron mínimos o despacho real."
            ),
        }
    # Exercise actual read endpoints with only authentication and clock overridden locally.
    import secrets

    os.environ["STOCKSMART_JWT_SECRET"] = secrets.token_urlsafe(48)
    from types import SimpleNamespace

    from app.api.auth import current_user
    from app.api.routes import get_now
    from app.main import app
    from app.persistence.database import get_session
    from fastapi.testclient import TestClient

    def session_override():
        with f() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[current_user] = lambda: SimpleNamespace(id=1, role="ADMIN")
    app.dependency_overrides[get_now] = lambda: REFERENCE
    try:
        with TestClient(app) as client:
            api_suppliers = client.get("/suppliers")
            api_prices = client.get(f"/prices/{item}")
            if api_suppliers.status_code != 200 or api_prices.status_code != 200:
                raise AssertionError(
                    f"HTTP falló: {api_suppliers.status_code}, {api_prices.status_code}"
                )
            assert {
                s["name"]: Decimal(str(s["shipping_cost_clp"])) for s in api_suppliers.json()
            } == COSTS
            api_options = api_prices.json()["comparison"]["options"]
            assert {
                r["supplier"]: Decimal(str(r["shipping_cost_clp"])) for r in api_options
            } == COSTS
            for row in api_options:
                assert (
                    Decimal(str(row["total_clp"]))
                    == Decimal(str(row["subtotal_clp"])) + COSTS[row["supplier"]]
                )
        evidence["comprobaciones"].append(
            "HTTP GET /suppliers y /prices/{item}: 200, costos y totales correctos; "
            "reloj y autenticación sustituidos en prueba local"
        )
    finally:
        app.dependency_overrides.clear()
    output = HERE / "resultados_locales"
    output.mkdir(exist_ok=True)
    (output / "verificacion_despacho.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("crear", "verificar"):
        raise SystemExit("Uso: python entorno_simulacion.py crear|verificar")
    (create if sys.argv[1] == "crear" else verify)()
