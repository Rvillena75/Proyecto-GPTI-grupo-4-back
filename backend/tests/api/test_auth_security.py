"""Every business operation enforces server-side identity, even without the UI."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import func, select

from app.persistence.models import Item, User
from app.services.auth import AUDIENCE, ISSUER, hash_password, issue_token, signing_key

WRITES = [
    ("post", "/items"),
    ("patch", "/items/1"),
    ("post", "/items/1/initial-stock"),
    ("put", "/items/1/safe-stock"),
    ("post", "/movements"),
    ("post", "/movements/1/reverse"),
    ("post", "/movements/1/corrections"),
    ("post", "/counts"),
    ("post", "/suppliers"),
    ("patch", "/suppliers/1"),
    ("post", "/suppliers/1/products"),
    ("patch", "/suppliers/1/products/1"),
    ("post", "/prices/1/refresh"),
    ("post", "/orders/1/receipt"),
    ("post", "/suggestions"),
    ("post", "/suggestions/1/decision"),
]


def warehouse(client, factory, password_hash):
    with factory() as session, session.begin():
        user = User(
            email="warehouse@example.test",
            name="Bodega",
            role="WAREHOUSE",
            password_hash=password_hash,
            is_active=True,
            created_at=datetime.now(UTC),
        )
        session.add(user)
        session.flush()
    token = issue_token(user)
    client.headers["Authorization"] = "Bearer " + token
    return user


@pytest.mark.parametrize("method,path", WRITES)
@pytest.mark.parametrize("token", [None, "invalid"])
def test_all_writes_reject_anonymous_or_invalid_token(api_client, clean_db, method, path, token):
    client, _ = api_client
    client.headers.pop("Authorization")
    if token:
        client.headers["Authorization"] = "Bearer " + token
    response = getattr(client, method)(path, json={})
    assert response.status_code == 401, (path, response.text)
    assert response.headers["www-authenticate"] == "Bearer"
    with clean_db() as session:
        assert session.scalar(select(func.count(Item.id))) == 0


@pytest.mark.parametrize("method,path", [WRITES[i] for i in (0, 1, 2, 3, 8, 9, 10, 11, 12, 14, 15)])
def test_warehouse_cannot_configure_or_decide(
    api_client, clean_db, test_password_hash, method, path
):
    client, _ = api_client
    warehouse(client, clean_db, test_password_hash)
    response = getattr(client, method)(path, json={})
    assert response.status_code == 403, response.text


def test_login_hash_actor_and_warehouse_operations(api_client, clean_db, test_password_hash):
    client, _ = api_client
    item = client.post(
        "/items",
        json={"name": "Leche", "base_unit": "L", "initial_quantity": "10", "initial_unit": "L"},
    ).json()
    actor = warehouse(client, clean_db, test_password_hash)
    token = client.post(
        "/auth/login",
        json={"email": " WAREHOUSE@example.test ", "password": "Test password only 2026!"},
    )
    assert token.status_code == 200
    assert token.json()["expires_in"] <= 3600
    client.headers["Authorization"] = "Bearer " + token.json()["access_token"]
    assert client.get("/auth/me").json()["role"] == "WAREHOUSE"
    move = client.post(
        "/movements", json={"item_id": item["id"], "kind": "EXIT", "quantity": "1", "unit": "L"}
    )
    assert move.status_code == 201
    assert move.json()["created_by"] == {"id": actor.id, "name": actor.name}
    count = client.post(
        "/counts", json={"item_id": item["id"], "physical_quantity": "12", "unit": "L"}
    )
    assert count.status_code == 201 and count.json()["needs_review"] is True
    assert count.json()["created_by"] == actor.id
    assert client.get(f"/items/{item['id']}/counts").json()[0]["needs_review"] is True
    assert client.get(f"/items/{item['id']}/alert").json()["base_unit"] == "L"
    with clean_db() as session:
        user = session.get(User, actor.id)
        assert user.password_hash.startswith("$2") and "Test password" not in user.password_hash
        user.is_active = False
        session.commit()
    assert client.get("/auth/me").status_code == 401


@pytest.mark.parametrize(
    "alteration", ["expired", "audience", "issuer", "missing_exp", "none", "sub"]
)
def test_jwt_validation(api_client, alteration):
    client, _ = api_client
    now = datetime.now(UTC)
    claims = {
        "sub": "1",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
        "iss": ISSUER,
        "aud": AUDIENCE,
    }
    if alteration == "expired":
        claims.update(
            iat=now - timedelta(hours=1),
            nbf=now - timedelta(hours=1),
            exp=now - timedelta(minutes=1),
        )
    if alteration == "audience":
        claims["aud"] = "another-api"
    if alteration == "issuer":
        claims["iss"] = "another-issuer"
    if alteration == "missing_exp":
        claims.pop("exp")
    if alteration == "sub":
        claims["sub"] = "9" * 100
    token = jwt.encode(
        claims,
        "" if alteration == "none" else signing_key(),
        algorithm="none" if alteration == "none" else "HS256",
    )
    client.headers["Authorization"] = "Bearer " + token
    assert client.get("/items").status_code == 401


def test_role_is_reloaded_and_cannot_be_assigned_by_request(api_client, clean_db):
    client, _ = api_client
    bad = client.post(
        "/auth/login", json={"email": "admin@example.test", "password": "wrong", "role": "ADMIN"}
    )
    assert bad.status_code == 422
    with clean_db() as session, session.begin():
        session.get(User, 1).role = "WAREHOUSE"
    assert client.post("/items", json={}).status_code == 403


def test_bad_login_is_generic_and_throttled(api_client):
    client, _ = api_client
    for _ in range(6):
        result = client.post(
            "/auth/login", json={"email": "missing@example.test", "password": "wrong"}
        )
        assert result.status_code == 401
        assert "missing" not in result.text
    assert (
        client.post(
            "/auth/login", json={"email": "missing@example.test", "password": "wrong"}
        ).status_code
        == 429
    )


@pytest.mark.parametrize("password", ["short", "á" * 40])
def test_password_byte_limits(password):
    from app.domain.errors import RuleViolation

    with pytest.raises(RuleViolation):
        hash_password(password)
