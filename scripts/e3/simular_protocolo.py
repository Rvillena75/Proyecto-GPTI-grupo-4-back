"""Run the approved, frozen E3 protocol through StockSmart services and PostgreSQL."""

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal as D
from pathlib import Path
from zoneinfo import ZoneInfo

from app.e2.dataset import json_text
from app.e2.replay import replay_inventory
from app.persistence.models import Order, PriceQuote, Suggestion, SupplierProduct, User
from app.services.catalog import CatalogService
from app.services.inventory import InventoryService
from app.services.orders import OrderService
from app.services.suggestions import SuggestionService
from local_check_db import local_database
from scraper.pipeline import compare_offers, load_products
from sqlalchemy import select, text

ZONE = ZoneInfo("America/Santiago")
STRESS_FIRST, STRESS_LAST = date(2026, 9, 14), date(2026, 9, 20)
DELAY_FIRST, DELAY_LAST = date(2026, 9, 15), date(2026, 9, 17)
ZERO = D(0)
START = date(2026, 7, 27)
END = date(2026, 10, 4)
OPENING = D("2.491")
BASE_PURCHASE = D("2.5")
SUPPLIER_IDS = (1, 2, 3)


def at(day, hour=0, minute=0):
    return datetime.combine(day, time(hour, minute), ZONE)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, content):
    path.write_text(json_text(content), encoding="utf-8")


def effective_receipt(day, stress):
    if stress and DELAY_FIRST <= day <= DELAY_LAST:
        return day + timedelta(days=2)
    return day


def build_inputs(data, suppliers, protocol_sha, dataset_sha):
    quotes = {q["supplier_id"]: q for q in data["price_quotes"]}
    products = {s["supplier_id"]: s for s in suppliers}
    if set(products) != set(SUPPLIER_IDS) or set(quotes) != set(SUPPLIER_IDS):
        raise ValueError("Expected exactly the three suppliers in the approved protocol")
    items = []
    approved = {
        1: (D("2.5"), 33875, 3990, 1),
        2: (D("0.125"), 2530, 4990, 1),
        3: (D("0.125"), 2570, 3990, 2),
    }
    for key in SUPPLIER_IDS:
        supplier, quote = products[key], quotes[key]
        pack = D(quote["pack_quantity"])
        assert (
            pack,
            D(quote["pack_price_clp"]),
            D(supplier["shipping_cost_clp"]),
            supplier["lead_time_days"],
        ) == approved[key]
        assert supplier["is_active"] and supplier["min_packs"] == 1
        items.append(
            {
                "key": key,
                "supplier": supplier["name"],
                "pack_kg": pack,
                "pack_clp": D(quote["pack_price_clp"]),
                "shipping_clp": D(supplier["shipping_cost_clp"]),
                "lead_days": supplier["lead_time_days"],
                "adapter": supplier["adapter"],
                "product": supplier["name_on_site"],
                "url": supplier["url"],
                "observation_source_at": quote["observed_at"],
                "evidence_sha256": quote["evidence_sha256"],
                "scrape_config": {k: supplier[k] for k in ("sku", "brand", "expected_name_tokens")}
                | {"pack": {**supplier["pack"], "is_variable_weight": False}},
            }
        )
    first = next(row for row in data["lots"] if row["receipt_id"] == data["receipts"][0]["id"])
    assert D(data["items"][0]["initial_quantity"]) == OPENING
    assert date.fromisoformat(data["demand"][0]["date"]) == START
    assert date.fromisoformat(data["demand"][-1]["date"]) == END
    assert len(data["demand"]) == 70
    assert sum(D(row["demand_quantity"]) for row in data["demand"]) == D("31.8")
    return {
        "dataset_sha256": dataset_sha,
        "protocolo_sha256": protocol_sha,
        "stock_inicial_kg": OPENING,
        "vencimiento_cerrado_apertura": first["closed_expires_at"],
        "vida_cerrada_desde_recepcion_dias": 68,
        "vida_abierta_dias": 5,
        "orden_del_dia": "Vencimiento 07:30, pedido fijo 09:00, recepción 10:00, "
        "vencimiento y consumo 18:00, conteo semanal 20:00, evaluación StockSmart 21:00",
        "proveedores": items,
        "referencia_precios": "Captura del 07/10, valor constante modelado",
    }


def case_input(common, frozen, stress, assisted):
    demand, manual = [], []
    for index, row in enumerate(frozen["demand"]):
        day = date.fromisoformat(row["date"])
        prior = D(row["demand_quantity"])
        up = stress and STRESS_FIRST <= day <= STRESS_LAST
        demand.append(
            {"date": day, "normal_kg": prior, "demand_kg": prior * (D("1.4") if up else 1)}
        )
        if not assisted and index % 5 == 0:
            supplier = common["proveedores"][(index // 5) % 3]
            planned = day + timedelta(days=supplier["lead_days"])
            manual.append(
                {
                    "issued": day,
                    "supplier_key": supplier["key"],
                    "required_kg": BASE_PURCHASE,
                    "planned": planned,
                    "arrival": effective_receipt(planned, stress),
                }
            )
    return common | {
        "estres": stress,
        "con_stocksmart": assisted,
        "demanda": demand,
        "pedidos_fijos": manual,
        "regla_atraso": "Recepción prevista 15/09 a 17/09 inclusive se traslada dos días. "
        "Se aplica una sola vez y a ambas políticas.",
    }


@dataclass
class Lot:
    number: int
    kg: D
    expires: datetime
    opened: bool = False


def store_catalog(factory, item_id, suppliers):
    for source in suppliers:
        with factory() as session:
            supplier = CatalogService(session).create_supplier(
                {
                    "name": source["supplier"],
                    "price_source_url": source["url"],
                    "lead_time_days": source["lead_days"],
                    "shipping_cost_clp": source["shipping_clp"],
                    "is_active": True,
                    "adapter": source["adapter"],
                },
                at(START),
            )
        with factory() as session:
            product = CatalogService(session).create_product(
                supplier.id,
                {
                    "item_id": item_id,
                    "name_on_site": source["product"],
                    "url": source["url"],
                    "pack_quantity": source["pack_kg"],
                    "min_packs": 1,
                    "is_active": True,
                    "scrape_config": source["scrape_config"],
                },
            )
        source["db_supplier"], source["db_product"] = supplier.id, product.id


def model_quotes(factory, suppliers, day):
    with factory() as session, session.begin():
        for source in suppliers:
            quote = PriceQuote(
                supplier_product_id=source["db_product"],
                pack_price_clp=source["pack_clp"],
                unit_price_clp=source["pack_clp"] / source["pack_kg"],
                available=True,
                observed_at=at(day),
                valid_until=at(day) + timedelta(days=7),
                evidence_snapshot={
                    "origen": "PRECIO_CONSTANTE_PROTOCOLO_SIMULADO",
                    "captura_real": source["observation_source_at"],
                },
                evidence_sha256=source["evidence_sha256"],
            )
            session.add(quote)
            session.flush()
            source["db_quote"] = quote.id


def all_options(suppliers, required):
    configs = [
        {
            "supplier_id": s["db_supplier"],
            "supplier_product_id": s["db_product"],
            "name": s["supplier"],
            "min_packs": 1,
            "is_active": True,
            "shipping_cost_clp": s["shipping_clp"],
            "pack": s["scrape_config"]["pack"],
        }
        for s in suppliers
    ]
    quotes = [
        {
            "id": s["db_quote"],
            "supplier_product_id": s["db_product"],
            "pack_price_clp": s["pack_clp"],
            "observed_at": s["observation_source_at"],
        }
        for s in suppliers
    ]
    return compare_offers(quotes, configs, required)["options"]


def materialize_manual(factory, item_id, actor_id, day, planned, required, option):
    # A manual external order is an input to the comparator. Record its approval directly;
    # receipts use the same operational endpoint service as StockSmart orders.
    with factory() as session, session.begin():
        suggestion = Suggestion(
            item_id=item_id,
            status="APPROVED",
            required_qty=required,
            in_transit_qty=ZERO,
            supplier_product_id=option["supplier_product_id"],
            packs=option["packs"],
            purchased_qty=D(option["purchased_quantity"]),
            subtotal_clp=D(option["subtotal_clp"]),
            shipping_cost_clp=D(option["shipping_cost_clp"]),
            total_clp=D(option["total_clp"]),
            inputs_snapshot={"source": "ORDEN_MANUAL_DEL_PROTOCOLO"},
            created_at=at(day, 9),
            created_by=actor_id,
            decided_by=actor_id,
            decided_at=at(day, 9, 5),
        )
        session.add(suggestion)
        session.flush()
        order = Order(
            code=f"SIM-MAN-{day:%Y%m%d}",
            status="APPROVED",
            suggestion_id=suggestion.id,
            decided_by=actor_id,
            decided_at=at(day, 9, 5),
            expected_at=at(planned, 10),
        )
        session.add(order)
        session.flush()
        return suggestion, order


def order_evidence(suggestion, order, source, options, stress):
    comparison = {opt["supplier"]: D(opt["total_clp"]) for opt in options}
    required = suggestion.required_qty
    mean = sum(comparison.values()) / 3
    second = suggestion.next_best_total_clp
    assert len(comparison) == 3 and suggestion.purchased_qty >= required
    assert suggestion.purchased_qty == source["pack_kg"] * suggestion.packs
    assert suggestion.total_clp == comparison[source["supplier"]]
    if second is not None:
        assert suggestion.total_clp == min(comparison.values())
        assert second == sorted(comparison.values())[1]
    planned = order.expected_at.astimezone(ZONE).date()
    arrival = effective_receipt(planned, stress)
    return {
        "issued": suggestion.created_at.astimezone(ZONE),
        "approved": order.decided_at.astimezone(ZONE),
        "planned": planned,
        "arrival": arrival,
        "delay_days": (arrival - planned).days,
        "order_id": order.id,
        "code": order.code,
        "suggestion_id": suggestion.id,
        "supplier": source["supplier"],
        "supplier_key": source["key"],
        "pack_kg": source["pack_kg"],
        "packs": suggestion.packs,
        "required_kg": required,
        "purchased_kg": suggestion.purchased_qty,
        "excess_kg": suggestion.purchased_qty - required,
        "subtotal_clp": suggestion.subtotal_clp,
        "shipping_clp": suggestion.shipping_cost_clp,
        "total_clp": suggestion.total_clp,
        "alternatives_clp": comparison,
        "mean_clp": mean,
        "saving_vs_mean": (mean - suggestion.total_clp) / mean,
        "next_best_clp": second,
        "saving_rn11": (second - suggestion.total_clp) / second if second is not None else None,
        "received_at": None,
        "receipt_movement_id": None,
    }


def simulate(factory, inputs):
    with factory() as session, session.begin():
        actor = User(
            email="simulacion-e3@example.test",
            password_hash="!simulation-disabled",
            name="Administrador simulado",
            role="ADMIN",
            is_active=False,
            created_at=at(START),
        )
        session.add(actor)
        session.flush()
        actor_id = actor.id
    with factory() as session:
        item_id = (
            InventoryService(session, actor_id)
            .create_item(
                "Pechuga de pollo asada E3", "kg", OPENING, "kg", ZONE.key, None, at(START)
            )
            .id
        )
    suppliers = [dict(row) for row in inputs["proveedores"]]
    store_catalog(factory, item_id, suppliers)
    by_key = {s["key"]: s for s in suppliers}
    lots = [Lot(0, OPENING, datetime.fromisoformat(inputs["vencimiento_cerrado_apertura"]))]
    orders, daily, events = [], [], []
    manual = {o["issued"]: o for o in inputs["pedidos_fijos"]}

    def stock():
        return sum((lot.kg for lot in lots), ZERO)

    def count(now, cause, expected_shortage):
        with factory() as session:
            count_row = InventoryService(session, actor_id).record_count(
                item_id, stock(), "kg", now, cause
            )
        assert count_row.shortage == expected_shortage
        events.append(
            {
                "tipo": "conteo",
                "fecha": now,
                "causa": cause,
                "id": count_row.id,
                "movimiento_id": count_row.adjustment_movement_id,
                "baja_kg": count_row.shortage,
            }
        )

    def expire(now):
        expired = ZERO
        for lot in lots:
            if lot.kg and lot.expires <= now:
                expired += lot.kg
                lot.kg = ZERO
        if expired:
            count(now, "vencimiento", expired)
        return expired

    for i, row in enumerate(inputs["demanda"]):
        day = row["date"]
        if i % 7 == 0:
            model_quotes(factory, suppliers, day)
        opening = stock()
        waste = expire(at(day, 7, 30))
        if day in manual:
            planned = manual[day]
            source = by_key[planned["supplier_key"]]
            opts = all_options(suppliers, planned["required_kg"])
            option = next(o for o in opts if o["supplier_id"] == source["db_supplier"])
            suggestion, order = materialize_manual(
                factory, item_id, actor_id, day, planned["planned"], planned["required_kg"], option
            )
            orders.append(order_evidence(suggestion, order, source, opts, inputs["estres"]))
            assert orders[-1]["arrival"] == planned["arrival"]
        received = ZERO
        for evidence in orders:
            if evidence["arrival"] != day:
                continue
            with factory() as session:
                receipt = OrderService(session, actor_id).receive(evidence["order_id"], at(day, 10))
            assert receipt["received_quantity"] == evidence["purchased_kg"]
            evidence["received_at"] = receipt["received_at"].astimezone(ZONE)
            evidence["receipt_movement_id"] = receipt["receipt_movement_id"]
            received += receipt["received_quantity"]
            closed = at(day + timedelta(days=inputs["vida_cerrada_desde_recepcion_dias"]))
            for _ in range(evidence["packs"]):
                lots.append(Lot(len(lots), evidence["pack_kg"], closed))

        demand_at = at(day, 18)
        waste += expire(demand_at)
        demand = row["demand_kg"]
        served = min(stock(), demand)
        left = served
        for lot in sorted(lots, key=lambda p: (p.expires, p.number)):
            take = min(lot.kg, left)
            if take and not lot.opened:
                lot.opened = True
                lot.expires = min(
                    lot.expires, demand_at + timedelta(days=inputs["vida_abierta_dias"])
                )
            lot.kg -= take
            left -= take
            if not left:
                break
        assert not left
        if served:
            with factory() as session:
                InventoryService(session, actor_id).add_movement(
                    item_id, "EXIT", served, "kg", demand_at, demand_at, "Demanda atendida E3"
                )
        if i % 7 == 6:
            count(at(day, 20), "desconocida", ZERO)
        now = at(day, 21)
        with factory() as session:
            state = InventoryService(session).status(item_id, now)
        assessment = state["assessment"]
        assert state["current_stock"] == stock() == opening + received - served - waste
        if inputs["con_stocksmart"] and assessment.alert_status == "LOW_STOCK":
            # A low-stock day remains visible even when the calculation requires zero kg.
            events.append(
                {"tipo": "alerta", "fecha": now, "requerido_kg": assessment.suggested_quantity}
            )
            if assessment.suggested_quantity:
                with factory() as session:
                    suggestion = SuggestionService(session, actor_id).generate(item_id, now)
                opts = all_options(suppliers, suggestion.required_qty)
                snapshot_opts = suggestion.inputs_snapshot["prices"]["comparison"]["options"]
                assert {p["supplier"]: D(p["total_clp"]) for p in snapshot_opts} == {
                    p["supplier"]: D(p["total_clp"]) for p in opts
                }
                events.append({"tipo": "sugerencia", "fecha": now, "id": suggestion.id})
                with factory() as session:
                    SuggestionService(session, actor_id).decide(
                        suggestion.id, "APPROVE", None, at(day, 21, 5)
                    )
                with factory() as session:
                    order = session.scalar(
                        select(Order).where(Order.suggestion_id == suggestion.id)
                    )
                    sid = session.get(SupplierProduct, suggestion.supplier_product_id).supplier_id
                source = next(s for s in suppliers if s["db_supplier"] == sid)
                evidence = order_evidence(suggestion, order, source, opts, inputs["estres"])
                orders.append(evidence)
                events.append({"tipo": "aprobacion", "fecha": evidence["approved"], "id": order.id})
        daily.append(
            {
                "date": day,
                "opening_kg": opening,
                "received_kg": received,
                "demand_kg": demand,
                "fulfilled_kg": served,
                "unmet_kg": demand - served,
                "waste_kg": waste,
                "closing_kg": stock(),
                "quiebre": demand > served,
                "consumption_status": state["consumption"].status,
                "weekly_kg": state["consumption"].weekly_average,
                "safe_kg": assessment.safe_stock,
                "projected_kg": assessment.projected_stock_at_arrival,
                "transit_kg_before_decision": assessment.in_transit_quantity,
                "alert": assessment.alert_status,
            }
        )
    assert (
        sum(event["tipo"] == "conteo" and event["causa"] == "desconocida" for event in events) == 10
    )
    assert all(
        (order["arrival"] > END) == (order["receipt_movement_id"] is None) for order in orders
    )
    return {"daily": daily, "orders": orders, "events": events, "item_id": item_id}


def metrics(result, assisted):
    daily, orders = result["daily"], result["orders"]
    return {
        "quiebres": sum(r["unmet_kg"] > 0 for r in daily),
        "demanda_kg": sum(r["demand_kg"] for r in daily),
        "no_atendida_kg": sum(r["unmet_kg"] for r in daily),
        "merma_kg": sum(r["waste_kg"] for r in daily),
        "stock_final_kg": daily[-1]["closing_kg"],
        "comprados_kg": sum(o["purchased_kg"] for o in orders),
        "excedente_compra_kg": sum(o["excess_kg"] for o in orders),
        "pedidos": len(orders),
        "pedidos_por_proveedor": dict(Counter(o["supplier"] for o in orders)),
        "pedidos_en_transito": sum(o["received_at"] is None for o in orders),
        "desembolso_clp": sum(o["total_clp"] for o in orders),
        "atrasos": sum(o["delay_days"] > 0 for o in orders),
        "ahorro_orden": sum(o["saving_vs_mean"] for o in orders) / len(orders)
        if assisted and orders
        else None,
        "ahorro_rn11": sum(o["saving_rn11"] for o in orders) / len(orders)
        if assisted and orders
        else None,
    }


def reduction(baseline, treatment):
    return (baseline - treatment) / D(baseline) if baseline else None


def execute(args):
    folder = args.dataset
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["files"]
    for file, digest in manifest.items():
        if sha(folder / file) != digest:
            raise ValueError(f"Original congelado no coincide con manifiesto: {file}")
    frozen = json.loads((folder / "dataset.json").read_text(encoding="utf-8"))
    common = build_inputs(
        frozen, load_products(args.parametros), sha(args.protocolo), sha(folder / "dataset.json")
    )
    args.output.mkdir(parents=True, exist_ok=True)
    inputs_dir = args.output / "entradas"
    inputs_dir.mkdir(exist_ok=True)
    identifiers = ("sin", "con", "sin_estres", "con_estres")
    if any((inputs_dir / f"{case}.json").exists() for case in identifiers):
        raise ValueError("Use an empty results directory to preserve prior inputs")
    cases = {
        case: case_input(common, frozen, case.endswith("estres"), case.startswith("con"))
        for case in identifiers
    }
    for case, case_data in cases.items():
        write(inputs_dir / f"{case}.json", case_data)
    original_name = f"stocksmart_e3_{args.run}_historico_test"
    historic = replay_inventory(frozen, local_database(original_name))
    results = {}
    for case, case_data in cases.items():
        dbname = f"stocksmart_e3_{args.run}_{case}_test"
        factory = local_database(dbname)
        result = simulate(factory, case_data)
        with factory() as session:
            version = session.execute(text("SHOW server_version")).scalar_one()
            migration = session.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        results[case] = result | {
            "database": dbname,
            "postgres_version": version,
            "migration": migration,
            "metrics": metrics(result, case.startswith("con")),
            "input_sha256": sha(inputs_dir / f"{case}.json"),
        }
        print(case, results[case]["metrics"], flush=True)
    targets = {}
    for pair, base, smart in (("normal", "sin", "con"), ("estres", "sin_estres", "con_estres")):
        b, s = results[base]["metrics"], results[smart]["metrics"]
        assert b["demanda_kg"] == s["demanda_kg"] == D("32.85" if pair == "estres" else "31.8")
        assert b["pedidos"] == 14 and list(b["pedidos_por_proveedor"].values()) == [5, 5, 4]
        targets[pair] = {
            "quiebres_reduccion": reduction(b["quiebres"], s["quiebres"]),
            "merma_reduccion": reduction(b["merma_kg"], s["merma_kg"]),
            "ahorro_orden": s["ahorro_orden"],
            "ahorro_rn11": s["ahorro_rn11"],
        }
    commit = subprocess.check_output(
        ["git", "-C", str(Path(__file__).resolve().parents[2]), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    write(
        args.output / "corridas.json",
        {
            "fuentes": common,
            "run_id": args.run,
            "backend_commit": commit,
            "simulator_sha256": sha(Path(__file__)),
            "historico_database": original_name,
            "historico_original": historic,
            "corridas": results,
            "indicadores": targets,
            "oficial": {
                "quiebres": targets["estres"]["quiebres_reduccion"],
                "merma": targets["normal"]["merma_reduccion"],
                "ahorro": targets["normal"]["ahorro_orden"],
            },
        },
    )
    assert all(sha(folder / file) == digest for file, digest in manifest.items())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "parametros", "protocolo", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--run", required=True)
    execute(parser.parse_args())
