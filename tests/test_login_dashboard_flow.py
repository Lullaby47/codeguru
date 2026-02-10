import os

from fastapi.testclient import TestClient


# Ensure SECRET_KEY is set before the app (and security module) are imported.
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from app.main import app  # noqa: E402


client = TestClient(app)


def test_login_then_dashboard_ok():
    """
    End-to-end smoke test:
    - Signup via auth API (idempotent: tolerate already-registered email).
    - Login via web form route (/login).
    - Follow with GET /dashboard which should return 200 when cookie handling works.
    """
    email = "testuser@example.com"
    username = "testuser"
    password = "password123"

    # Create user (or tolerate "already registered" error)
    signup_resp = client.post(
        "/auth/signup",
        data={"email": email, "username": username, "password": password},
    )
    assert signup_resp.status_code in (200, 400)

    # Login via web route; TestClient will manage cookies for us.
    login_resp = client.post(
        "/login",
        data={"email_or_username": username, "password": password},
        allow_redirects=False,
    )

    assert login_resp.status_code == 303
    assert login_resp.headers.get("location") == "/dashboard"

    # Now access dashboard; should be authenticated.
    dashboard_resp = client.get("/dashboard")
    assert dashboard_resp.status_code == 200


