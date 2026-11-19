"""Seed and verify the four PR-04 checks on a dedicated loopback PostgreSQL database."""

import argparse
import json
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal as D
from pathlib import Path
from zoneinfo import ZoneInfo

from app.persistence.models import Item, Movement
from app.services.inventory import InventoryService
from local_check_db import local_database
from sqlalchemy import select

ZONE = ZoneInfo("America/Santiago")


def at(day, hour=0):
    return datetime.combine(day, time(hour), ZONE)


def seed(factory, name, start, daily, *, through=None):
    with factory() as session:
        existing = session.scalar(select(Item).where(Item.name == name))
    if existing is None:
        with factory() as session:
            existing = InventoryService(session).create_item(
                name, "kg", D(40), "kg", ZONE.key, None, at(start)
            )
    with factory() as session:
        events = session.scalars(
            select(Movement).where(Movement.item_id == existing.id, Movement.kind == "EXIT")
        ).all()
        actual = [(row.occurred_at, -row.delta) for row in events]
    wanted = [(at(day, 9), D(quantity)) for day, quantity in daily]
    if len(actual) != len(set(actual)) or any(event not in wanted for event in actual):
        raise ValueError(f"Unexpected history in {name}; no records were modified")
    for occurred_at, quantity in wanted:
        if (through is None or occurred_at <= through) and (occurred_at, quantity) not in actual:
            with factory() as session:
                InventoryService(session).add_movement(
                    existing.id, "EXIT", quantity, "kg", occurred_at, occurred_at, "E3 PR-04"
                )
    return existing.id


def verify(output: Path, reference_date: date):
    database = "stocksmart_e3_pr04_test"
    factory = local_database(database, reuse=True)
    reference = at(reference_date, 8)
    start = reference_date - timedelta(days=28)
    daily = [(start + timedelta(days=i), 1) for i in range(28)]
    label = f"E3 PR-04 {reference_date.isoformat()}"
    planned = daily + [(reference_date, 2)]
    regular = seed(factory, f"{label} 28 días", start, planned, through=reference)
    preliminary_start = reference_date - timedelta(days=3)
    preliminary = seed(
        factory,
        f"{label} preliminar",
        preliminary_start,
        [(preliminary_start + timedelta(days=i), 2) for i in range(3)],
    )
    insufficient = seed(factory, f"{label} sin día completo", reference_date, [])

    def status(item_id, ref):
        with factory() as session:
            return InventoryService(session).status(item_id, ref)

    before = status(regular, reference)
    seed(factory, f"{label} 28 días", start, planned)
    after = status(regular, at(reference_date, 10))
    partial = status(preliminary, reference)
    none = status(insufficient, reference)
    consumption = after["consumption"]
    assert consumption == before["consumption"]
    assert consumption.status == "ESTABLISHED" and consumption.complete_days == 28
    assert consumption.observed_quantity == 28 and consumption.weekly_average == 7
    assert after["assessment"].daily_average == 1 and after["assessment"].safe_stock == 7
    assert after["assessment"].target_stock == 14
    assert before["assessment"].daily_average == 1
    assert partial["consumption"].status == "PRELIMINARY"
    assert partial["consumption"].complete_days == 3 and partial["assessment"].daily_average == 2
    assert none["consumption"].status == "INSUFFICIENT_HISTORY"
    assert none["assessment"].daily_average is None
    with factory() as session:
        rows = session.scalars(
            select(Movement)
            .where(
                Movement.item_id == regular,
                Movement.kind == "EXIT",
            )
            .order_by(Movement.occurred_at)
        ).all()
        assert [(r.occurred_at.astimezone(ZONE), -r.delta) for r in rows[:-1]] == [
            (at(day, 9), D(quantity)) for day, quantity in daily
        ]
        assert len(rows) == 29 and rows[-1].delta == -2
        movements = [
            {"id": r.id, "fecha": r.occurred_at.astimezone(ZONE), "kg": -r.delta} for r in rows
        ]
    evidence = {
        "resultado": "APROBADO_LOCAL",
        "database": database,
        "referencia": reference,
        "insumo_28_dias_id": regular,
        "insumo_preliminar_id": preliminary,
        "insumo_sin_dia_id": insufficient,
        "observacion_inicio": at(start),
        "consumo": asdict(consumption),
        "reposicion": asdict(after["assessment"]),
        "consumo_preliminar": asdict(partial["consumption"]),
        "consumo_sin_dia": asdict(none["consumption"]),
        "salidas": movements,
        "control_salida_hoy_kg": 2,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, default=str, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"PR-04 PostgreSQL: {regular=}, 28 kg / 28 días = 1 kg/día, 7 kg/semana.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fecha", type=date.fromisoformat, default=date(2026, 11, 18))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    verify(args.output, args.fecha)
