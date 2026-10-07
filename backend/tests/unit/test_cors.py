"""CORS wiring: the deployed frontend can call the API; other origins cannot."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import cors_origins
from app.main import add_cors

FRONTEND = "https://stocksmart-nine.vercel.app"


def client_for(origins: list[str]) -> TestClient:
    application = FastAPI()

    @application.get("/ping")
    def ping() -> dict:
        return {"ok": True}

    add_cors(application, origins)
    return TestClient(application)


def test_origins_are_read_from_a_comma_separated_variable(monkeypatch) -> None:
    monkeypatch.setenv("STOCKSMART_CORS_ORIGINS", f" {FRONTEND}/ , http://localhost:5173 ,")
    assert cors_origins() == [FRONTEND, "http://localhost:5173"]


def test_no_variable_means_no_cross_origin_calls(monkeypatch) -> None:
    monkeypatch.delenv("STOCKSMART_CORS_ORIGINS", raising=False)
    assert cors_origins() == []


def test_preflight_from_the_frontend_is_allowed() -> None:
    response = client_for([FRONTEND]).options(
        "/ping",
        headers={
            "Origin": FRONTEND,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, Content-Type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONTEND


def test_unknown_origin_gets_no_cors_header() -> None:
    response = client_for([FRONTEND]).get("/ping", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers
