from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_exact_local_frontend_origins_are_allowed() -> None:
    for origin in ("http://localhost:3000", "http://127.0.0.1:3000"):
        response = client.get("/health", headers={"Origin": origin})

        assert response.headers["access-control-allow-origin"] == origin


def test_unconfigured_local_frontend_origin_is_rejected() -> None:
    response = client.get(
        "/health",
        headers={"Origin": "http://127.0.0.1:3001"},
    )

    assert "access-control-allow-origin" not in response.headers
