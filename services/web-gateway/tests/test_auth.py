import hashlib
import importlib

from fastapi.testclient import TestClient

def test_passphrase_session_and_csrf_logout(monkeypatch, tmp_path):
    salt = "test-salt-value-0123456789"
    phrase = "correct horse battery staple"
    digest = hashlib.scrypt(phrase.encode(), salt=salt.encode(), n=2**14, r=8, p=1).hex()
    (tmp_path / "index.html").write_text("app")
    monkeypatch.setenv("LIBRARY_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("LIBRARY_TOKEN", "x" * 40)
    monkeypatch.setenv("TEAM_PASSPHRASE_SALT", salt)
    monkeypatch.setenv("TEAM_PASSPHRASE_HASH", digest)
    monkeypatch.setenv("GATEWAY_LOCAL_MODE", "true")
    monkeypatch.setenv("GATEWAY_ALLOWED_HOSTS", "127.0.0.1:8080")
    monkeypatch.setenv("GATEWAY_ORIGIN", "http://127.0.0.1:8080")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path))
    create_app = importlib.import_module("gateway.main").create_app
    with TestClient(create_app(), base_url="http://127.0.0.1:8080") as client:
        assert client.get("/auth/session").status_code == 401
        assert client.post("/auth/login", json={"passphrase": "incorrect phrase"}).status_code == 401
        denied_origin = client.post(
            "/auth/login", json={"passphrase": phrase}, headers={"Origin": "http://127.0.0.1:5174"}
        )
        assert denied_origin.status_code == 403
        localhost_login = client.post(
            "/auth/login", json={"passphrase": phrase}, headers={"Origin": "http://localhost:5173"}
        )
        assert localhost_login.status_code == 200
        login = client.post(
            "/auth/login", json={"passphrase": phrase}, headers={"Origin": "http://127.0.0.1:5173"}
        )
        assert login.status_code == 200
        assert "HttpOnly" in login.headers["set-cookie"]
        csrf = client.get("/auth/session").json()["csrf_token"]
        assert client.post("/auth/logout").status_code == 403
        assert client.post("/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
        assert client.get("/auth/session").status_code == 401
