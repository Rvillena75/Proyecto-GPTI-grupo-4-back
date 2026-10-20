"""Exercise an installed wheel over HTTP against a new disposable LOCAL PostgreSQL database.

Run with the production-only virtualenv's Python (-I recommended). No remote database,
operational data, public supplier request, default account or deployment is involved.
"""

import importlib.util
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


class SmokeFailure(Exception):
    """A controlled diagnostic safe to include in CI output."""


def request(base, path, status=200, payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None if payload is None else json.dumps(payload).encode()
    call = Request(base + path, data=data, headers=headers)
    try:
        response = urlopen(call, timeout=5)
    except HTTPError as exc:
        response = exc
    with response:
        actual = response.status
        if actual != status:
            raise SmokeFailure(f"{path}: expected HTTP {status}, got {actual}")
        try:
            return json.loads(response.read())
        except (ValueError, UnicodeError):
            raise SmokeFailure(f"{path}: invalid JSON") from None


def main():
    phase = "validate isolated environment"
    source = None
    target = None
    server = None
    name = "stocksmart_deploy_" + uuid4().hex[:16] + "_test"
    created = False
    report = {}
    try:
        source_url = make_url(os.environ["STOCKSMART_TEST_DATABASE_URL"])
        if (
            source_url.get_backend_name() != "postgresql"
            or not (source_url.database or "").endswith("_test")
            or source_url.host not in {"127.0.0.1", "localhost", "::1"}
        ):
            raise SmokeFailure("Use an isolated loopback PostgreSQL *_test source")
        build = os.getenv("STOCKSMART_BUILD_SHA", "")
        if not re.fullmatch("[0-9a-fA-F]{40}", build):
            raise SmokeFailure("STOCKSMART_BUILD_SHA must identify the tested Git commit")
        if importlib.util.find_spec("pytest") is not None:
            raise SmokeFailure("Use a production-only virtualenv without pytest")
        import app

        if not Path(app.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
            raise SmokeFailure("Application must be imported from the installed wheel")
        from app.persistence.models import User
        from app.services.auth import hash_password

        source = create_engine(source_url, isolation_level="AUTOCOMMIT")
        phase = "create disposable database"
        with source.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        created = True
        target_url = source_url.set(database=name)
        target = create_engine(target_url, connect_args={"connect_timeout": 3})
        backend = Path(__file__).resolve().parents[1] / "backend"
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env.update(
            STOCKSMART_DATABASE_URL=target_url.render_as_string(hide_password=False),
            STOCKSMART_JWT_SECRET=secrets.token_urlsafe(48),
            STOCKSMART_ENVIRONMENT="production",
            STOCKSMART_BUILD_SHA=build,
            STOCKSMART_CORS_ORIGINS="https://stocksmart-nine.vercel.app",
        )
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with TemporaryDirectory(prefix="stocksmart-deploy-smoke-") as directory:
            lab = Path(directory)
            shutil.copyfile(backend / "alembic.ini", lab / "alembic.ini")
            shutil.copytree(backend / "migrations", lab / "migrations")
            phase = "migrate installed application"
            with (lab / "migrations.log").open("w", encoding="utf-8") as migration_log:
                subprocess.run(
                    [sys.executable, "-I", "-m", "alembic", "upgrade", "head"],
                    cwd=lab,
                    env=env,
                    stdout=migration_log,
                    stderr=subprocess.STDOUT,
                    timeout=60,
                    check=True,
                    creationflags=flags,
                )
            password = secrets.token_urlsafe(24)
            phase = "seed disposable test identity"
            with Session(target) as session, session.begin():
                session.add(
                    User(
                        email="deployment-smoke@example.test",
                        name="Isolated deployment check",
                        password_hash=hash_password(password),
                        role="ADMIN",
                        is_active=True,
                        created_at=datetime.now(UTC),
                    )
                )
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                env["PORT"] = str(probe.getsockname()[1])
            phase = "start production HTTP server"
            with (lab / "http.log").open("w", encoding="utf-8") as server_log:
                server = subprocess.Popen(
                    [
                        sys.executable,
                        "-I",
                        "-m",
                        "uvicorn",
                        "app.main:app",
                        "--host",
                        "0.0.0.0",
                        "--port",
                        env["PORT"],
                    ],
                    cwd=lab,
                    env=env,
                    stdout=server_log,
                    stderr=subprocess.STDOUT,
                    creationflags=flags,
                )
                try:
                    base = "http://127.0.0.1:" + env["PORT"]
                    deadline = time.monotonic() + 30
                    while True:
                        if server.poll() is not None:
                            raise SmokeFailure("Installed HTTP server exited during startup")
                        try:
                            if request(base, "/ready") == {"status": "ok"}:
                                break
                        except (URLError, TimeoutError, ConnectionError):
                            pass
                        if time.monotonic() >= deadline:
                            raise SmokeFailure("Readiness did not become healthy within 30 seconds")
                        time.sleep(0.15)
                    phase = "verify readiness, identity and authorization"
                    if request(base, "/live") != {"status": "alive"}:
                        raise SmokeFailure("Liveness response mismatch")
                    version = request(base, "/version")
                    if (
                        version["commit"] != build
                        or version["expected_schema_revision"] != "0005_e2_completion"
                    ):
                        raise SmokeFailure("Build/schema identity mismatch")
                    request(base, "/items", status=401)
                    credentials = request(
                        base,
                        "/auth/login",
                        payload={"email": "deployment-smoke@example.test", "password": password},
                    )
                    token = credentials["access_token"]
                    if request(base, "/auth/me", token=token)["role"] != "ADMIN":
                        raise SmokeFailure("Authenticated role mismatch")
                    phase = "verify authorized persistence over HTTP"
                    item = request(
                        base,
                        "/items",
                        status=201,
                        token=token,
                        payload={
                            "name": "Deployment check",
                            "base_unit": "kg",
                            "initial_quantity": "2",
                            "initial_unit": "kg",
                        },
                    )
                    request(
                        base,
                        "/movements",
                        status=201,
                        token=token,
                        payload={
                            "item_id": item["id"],
                            "kind": "ENTRY",
                            "quantity": "1",
                            "unit": "kg",
                        },
                    )
                    stock = request(base, f"/items/{item['id']}", token=token)
                    if Decimal(stock["current_stock"]) != 3:
                        raise SmokeFailure("Authorized write was not persisted correctly")
                    report = {
                        "status": "PASS",
                        "installed_wheel": True,
                        "production_dependencies_only": True,
                        "bind": "0.0.0.0:$PORT",
                        "migration": "0005_e2_completion",
                        "commit": build,
                        "checks": [
                            "ready",
                            "live",
                            "version",
                            "401 without token",
                            "login",
                            "role",
                            "authorized inventory write/read",
                        ],
                        "database": "disposable local PostgreSQL *_test",
                        "external_requests": False,
                        "deployment_performed": False,
                    }
                finally:
                    server.terminate()
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait(timeout=10)
                    server = None
    except Exception as exc:
        report = {"status": "ERROR", "phase": phase, "type": type(exc).__name__}
        if isinstance(exc, SmokeFailure):
            report["message"] = str(exc)
    finally:
        if server is not None:
            server.kill()
            server.wait(timeout=10)
        if target is not None:
            target.dispose()
        if source is not None:
            try:
                if created and re.fullmatch("stocksmart_deploy_[0-9a-f]{16}_test", name):
                    with source.connect() as connection:
                        connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
            except Exception as exc:
                report = {
                    "status": "ERROR",
                    "phase": "cleanup test database",
                    "type": type(exc).__name__,
                }
            finally:
                source.dispose()
    print(json.dumps(report))
    return 0 if report.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
