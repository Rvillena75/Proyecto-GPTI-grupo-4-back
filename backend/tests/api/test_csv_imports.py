"""DRS v1.4 REQ-18, RN-19; Plan v2 PR-18/19/20 against real PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

HEADER = "insumo,cantidad,unidad,tipo,fecha,observacion\n"
DATE = "2026-09-28T12:00:00Z"


def setup(api_client):
    client, clock = api_client
    item = client.post(
        "/items",
        json={
            "name": "Ají y limón",
            "base_unit": "kg",
            "initial_quantity": "5",
            "initial_unit": "kg",
        },
    ).json()
    return client, item


def upload(client, body):
    return client.post(
        "/movements/import-csv",
        content=body.encode("utf-8") if isinstance(body, str) else body,
        headers={"Content-Type": "text/csv"},
    )


def row(item, quantity="1", kind="ENTRADA", unit="kg", note="", date=DATE):
    return f"{item},{quantity},{unit},{kind},{date},{note}\n"


def stock(client, item):
    return Decimal(client.get(f"/items/{item['id']}").json()["current_stock"])


def test_plan_pr18_success_history_accents_and_replay(api_client):
    client, item = setup(api_client)
    body = HEADER + row(item["id"], "4", note='"Recepción, café\ny ají"')
    body += row(item["id"], "2", "SALIDA") + row(item["id"], "500", "SALIDA", "g")
    result = upload(client, "\ufeff" + body)
    assert result.status_code == 200, result.text
    assert result.json()["imported"] == 3
    assert stock(client, item) == Decimal("6.5")
    history = client.get(f"/items/{item['id']}/movements").json()
    assert len(history) == 4
    assert history[1]["note"] == "Recepción, café\ny ají"
    assert history[1]["created_by"]["id"] == 1
    assert upload(client, body).json()["replayed"] is True
    assert stock(client, item) == Decimal("6.5")


def test_plan_pr19_negative_stock_rolls_back_every_row(api_client):
    client, item = setup(api_client)
    body = HEADER + row(item["id"], "2") + row(item["id"], "3", "SALIDA")
    result = upload(client, body + row(item["id"], "6", "SALIDA"))
    assert result.status_code == 422
    assert result.json()["error"]["details"][0]["row"] == 3
    assert stock(client, item) == 5
    assert len(client.get(f"/items/{item['id']}/movements").json()) == 1


def test_plan_pr20_all_errors_and_no_writes(api_client):
    client, item = setup(api_client)
    body = HEADER + row(item["id"], "2") + row(999999) + row(item["id"], "8", "SALIDA")
    result = upload(client, body)
    assert result.status_code == 422
    assert [e["row"] for e in result.json()["error"]["details"]] == [2, 3]
    assert stock(client, item) == 5


@pytest.mark.parametrize(
    "body",
    [
        b"",
        HEADER,
        b"\xff",
        HEADER + "\n",
        "insumo,cantidad\n1,2",
        HEADER + row(1, "NaN"),
        HEADER + row(1, "-2"),
        HEADER + row(1, "0"),
        HEADER + row(1, unit="caja"),
        HEADER + row(1, date="ayer"),
        HEADER + row(1, date="2026-09-28T12:00:00"),
        HEADER + row(1, note='"sin cierre'),
        HEADER + row(1) + row(1),
        HEADER + row(1, note="x" * 2001),
    ],
)
def test_invalid_fields_and_duplicates_are_controlled(api_client, body):
    client, item = setup(api_client)
    result = upload(client, body)
    assert result.status_code == 422, result.text
    assert stock(client, item) == 5


def test_semicolon_decimal_comma_and_two_items(api_client):
    client, item = setup(api_client)
    second = client.post(
        "/items",
        json={"name": "Sal", "base_unit": "kg", "initial_quantity": "2", "initial_unit": "kg"},
    ).json()
    body = HEADER.replace(",", ";")
    body += f"{item['id']};1,5;kg;ENTRADA;{DATE};recepción\n"
    body += f"{second['id']};500;g;SALIDA;{DATE};consumo\n"
    assert upload(client, body).status_code == 200
    assert stock(client, item) == Decimal("6.5")
    assert stock(client, second) == Decimal("1.5")


def test_size_rows_authentication_and_role(api_client, clean_db):
    from sqlalchemy import update

    from app.persistence.models import User

    client, item = setup(api_client)
    assert upload(client, b"x" * (1_048_576 + 1)).status_code == 413
    body = HEADER + "".join(row(item["id"], note=str(i)) for i in range(1001))
    assert upload(client, body).status_code == 422
    assert client.post("/movements/import-csv", content=HEADER).status_code == 415
    with clean_db() as session, session.begin():
        session.execute(update(User).values(role="WAREHOUSE"))
    assert upload(client, HEADER + row(item["id"])).status_code == 200
    client.headers.pop("Authorization")
    assert upload(client, HEADER + row(item["id"])).status_code == 401


def test_concurrent_retries_apply_once(api_client):
    client, item = setup(api_client)
    body = HEADER + row(item["id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: upload(client, body), range(2)))
    assert [r.status_code for r in results] == [200, 200]
    assert sorted(r.json()["replayed"] for r in results) == [False, True]
    assert stock(client, item) == 6


def test_historical_and_future_integrity(api_client):
    client, item = setup(api_client)
    result = upload(
        client,
        HEADER
        + row(item["id"], date="2026-09-27T12:00:00Z")
        + row(item["id"], date="2026-09-29T12:00:00Z"),
    )
    assert result.status_code == 422
    assert [e["code"] for e in result.json()["error"]["details"]] == [
        "BEFORE_INITIAL_STOCK",
        "FUTURE_MOVEMENT",
    ]
    assert stock(client, item) == 5
