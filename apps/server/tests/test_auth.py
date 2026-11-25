"""应用鉴权：未登录一律 401，登录后放行，会话可退出与恢复。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import TEST_PASSWORD, login, make_settings
from forager.api.app import create_app
from forager.auth import hash_secret


def _client(tmp_path):
    return create_app(make_settings(tmp_path))


def _set_password(app, password: str = TEST_PASSWORD) -> None:
    app.state.db.execute(
        "UPDATE users SET password_hash = ? WHERE id = 1", (hash_secret(password),)
    )


def test_business_routes_require_login(tmp_path) -> None:
    app = _client(tmp_path)
    with TestClient(app) as client:
        _set_password(app)
        for path in (
            "/api/sources",
            "/api/library/items",
            "/api/fetchers",
            "/api/settings/sources",
        ):
            res = client.get(path)
            assert res.status_code == 401, path
            assert res.json()["error"] == "需要登录"


def test_health_stays_public(tmp_path) -> None:
    with TestClient(_client(tmp_path)) as client:
        assert client.get("/api/health").status_code == 200


def test_login_sets_httponly_cookie_and_unlocks(tmp_path) -> None:
    app = _client(tmp_path)
    with TestClient(app) as client:
        _set_password(app)
        res = client.post(
            "/api/session/login", json={"username": "admin", "password": TEST_PASSWORD}
        )
        assert res.status_code == 200
        cookie = res.headers["set-cookie"]
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie.replace("samesite", "SameSite")
        assert client.get("/api/sources").status_code == 200
        assert client.get("/api/session/status").json()["authenticated"] is True


def test_wrong_password_is_rejected(tmp_path) -> None:
    app = _client(tmp_path)
    with TestClient(app) as client:
        _set_password(app)
        res = client.post("/api/session/login", json={"username": "admin", "password": "nope"})
        assert res.status_code == 401
        assert "set-cookie" not in res.headers
        assert client.get("/api/sources").status_code == 401


def test_logout_invalidates_session(tmp_path) -> None:
    app = _client(tmp_path)
    with TestClient(app) as client:
        _set_password(app)
        login(client)
        assert client.get("/api/sources").status_code == 200
        assert client.post("/api/session/logout").status_code == 200
        assert client.get("/api/sources").status_code == 401


def test_session_survives_app_restart(tmp_path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        _set_password(app)
        login(client)
        token = client.cookies.get("forager_session")
    assert token

    app2 = create_app(settings)
    with TestClient(app2) as client:
        client.cookies.set("forager_session", token)
        assert client.get("/api/sources").status_code == 200


def test_change_password_kicks_other_sessions(tmp_path) -> None:
    app = _client(tmp_path)
    with TestClient(app) as client:
        _set_password(app)
        login(client)
        old = client.cookies.get("forager_session")
        res = client.post(
            "/api/session/change-password",
            json={"current": TEST_PASSWORD, "next": "new-password-456"},
        )
        assert res.status_code == 200
        new = client.cookies.get("forager_session")
        assert new and new != old

        other = TestClient(app)
        with other:
            other.cookies.set("forager_session", old)
            assert other.get("/api/sources").status_code == 401


def test_change_password_requires_current(tmp_path) -> None:
    app = _client(tmp_path)
    with TestClient(app) as client:
        _set_password(app)
        login(client)
        res = client.post(
            "/api/session/change-password", json={"current": "wrong", "next": "whatever123"}
        )
        assert res.status_code == 400


def test_recover_with_code(tmp_path) -> None:
    app = _client(tmp_path)
    code = "AAAA-BBBB-CCCC-DDDD"
    with TestClient(app) as client:
        _set_password(app)
        app.state.db.execute(
            "UPDATE users SET recovery_hash = ? WHERE id = 1", (hash_secret(code),)
        )
        login(client)

        res = client.post(
            "/api/session/recover", json={"recoveryCode": code.lower(), "nextPassword": "fresh-pass-789"}
        )
        assert res.status_code == 200
        assert client.get("/api/sources").status_code == 200

        bad = client.post(
            "/api/session/recover", json={"recoveryCode": "NOPE", "nextPassword": "fresh-pass-789"}
        )
        assert bad.status_code == 401


def test_short_password_rejected_on_recover(tmp_path) -> None:
    app = _client(tmp_path)
    code = "EEEE-FFFF-0000-1111"
    with TestClient(app) as client:
        app.state.db.execute(
            "UPDATE users SET recovery_hash = ? WHERE id = 1", (hash_secret(code),)
        )
        res = client.post(
            "/api/session/recover", json={"recoveryCode": code, "nextPassword": "short"}
        )
        assert res.status_code == 400
