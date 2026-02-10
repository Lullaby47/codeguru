import os

from fastapi.testclient import TestClient


os.environ.setdefault("SECRET_KEY", "test-secret-key")

from app.main import app  # noqa: E402


client = TestClient(app)


def test_auth_api_returns_json_not_html():
    # Intentionally send invalid credentials; we only care about content type.
    resp = client.post(
        "/auth/login",
        data={"email_or_username": "nouser", "password": "bad"},
        allow_redirects=False,
    )
    ct = resp.headers.get("content-type", "")
    # FastAPI error responses are JSON by default
    assert "application/json" in ct.lower()


def test_web_login_returns_html_on_get():
    resp = client.get("/login")
    ct = resp.headers.get("content-type", "")
    assert "text/html" in ct.lower()


def test_auth_get_login_redirects_to_web_login():
    resp = client.get("/auth/login", allow_redirects=False)
    assert resp.status_code in (302, 303)
    assert resp.headers.get("location") == "/login"


