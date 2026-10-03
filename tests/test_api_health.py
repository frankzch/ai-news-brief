from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_health_ok_without_database():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def _preflight(origin):
    return client.options(
        "/api/health",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )


def test_cors_allows_vite_dev_origin():
    for origin in ("http://localhost:5173", "http://127.0.0.1:5173"):
        r = _preflight(origin)
        assert r.headers.get("access-control-allow-origin") == origin
        assert "access-control-allow-credentials" not in r.headers


def test_cors_rejects_other_origins():
    r = _preflight("http://evil.example")
    assert "access-control-allow-origin" not in r.headers


def test_cors_methods_are_read_only():
    r = _preflight("http://localhost:5173")
    allowed = r.headers.get("access-control-allow-methods", "")
    assert "POST" not in allowed and "DELETE" not in allowed


def test_unhandled_error_is_generic():
    @app.get("/api/_boom")
    def boom():
        raise RuntimeError("postgres://user:secret@host/db")

    r = TestClient(app, raise_server_exceptions=False).get("/api/_boom")
    assert r.status_code == 500
    assert "secret" not in r.text
