import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from flashcard import auth as signin
from flashcard.config import Settings
from flashcard.web import create_app

from .conftest import TOKEN

CLIENT_ID = "cid.apps.googleusercontent.com"


@pytest.fixture
def google_settings(tmp_path):
    return Settings(
        _env_file=None,
        host="0.0.0.0",
        sqlite_path=str(tmp_path / "unused.db"),
        auth="google",
        google_client_id=CLIENT_ID,
        google_client_secret="secret",
        allowed_emails="Me@Example.com, friend@example.com",
        session_secret="s" * 40,
        mcp_tokens=f"claude={TOKEN}",
        public_host="flashcard.example.com",
    )


@pytest.fixture
def client(google_settings, engine):
    app = create_app(google_settings, engine)
    with TestClient(app, base_url="https://flashcard.example.com", follow_redirects=False) as c:
        yield c


def _claims(nonce, email="me@example.com"):
    return {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": time.time() + 300,
        "nonce": nonce,
        "email": email,
        "email_verified": True,
    }


def _sign_in(client, monkeypatch, email="me@example.com", next_path="/"):
    start = client.get("/auth/login", params={"next": next_path})
    assert start.status_code == 303
    query = parse_qs(urlsplit(start.headers["location"]).query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == ["https://flashcard.example.com/auth/callback"]
    state, nonce = query["state"][0], query["nonce"][0]
    seen = {}

    def fake_exchange(code, client_id, client_secret, redirect_uri):
        seen.update(code=code, secret=client_secret, redirect=redirect_uri)
        return _claims(nonce, email)

    monkeypatch.setattr(signin, "exchange_code", fake_exchange)
    resp = client.get("/auth/callback", params={"code": "abc", "state": state})
    assert seen == {
        "code": "abc",
        "secret": "secret",
        "redirect": "https://flashcard.example.com/auth/callback",
    }
    return resp


def test_unsigned_get_redirects_to_login(client):
    resp = client.get("/browse?q=x")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/auth/login?next=/browse%3Fq%3Dx"
    assert client.post("/add", data={"front": "a", "back": "b"}).status_code == 401


def test_sign_in_flow_and_logout(client, monkeypatch):
    resp = _sign_in(client, monkeypatch, next_path="/stats")
    assert resp.status_code == 303 and resp.headers["location"] == "/stats"
    home = client.get("/")
    assert home.status_code == 200 and "me@example.com" in home.text
    assert client.post("/add", data={"deck": "it", "front": "CAP", "back": "theorem"}).status_code == 303
    assert client.post("/auth/logout").status_code == 200
    assert client.get("/").status_code == 303


def test_created_by_is_google_email(client, monkeypatch, engine):
    from sqlalchemy import select

    from flashcard.db import cards

    _sign_in(client, monkeypatch)
    client.post("/add", data={"deck": "it", "front": "ACID", "back": "tx"})
    with engine.connect() as conn:
        assert conn.execute(select(cards.c.created_by)).scalar_one() == "web:me@example.com"


def test_email_not_allowed(client, monkeypatch):
    resp = _sign_in(client, monkeypatch, email="stranger@example.com")
    assert resp.status_code == 403 and "stranger@example.com" in resp.text
    assert client.get("/").status_code == 303


def test_state_mismatch_rejected(client, monkeypatch):
    client.get("/auth/login")
    monkeypatch.setattr(signin, "exchange_code", lambda *a: pytest.fail("must not exchange"))
    assert client.get("/auth/callback", params={"code": "abc", "state": "forged"}).status_code == 400


def test_open_redirect_blocked(client, monkeypatch):
    resp = _sign_in(client, monkeypatch, next_path="//evil.example.com/")
    assert resp.headers["location"] == "/"


def test_mcp_unaffected_by_google_sign_in(client):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    resp = client.post(f"/m/{TOKEN}/mcp", json=body, headers={**headers, "Host": "flashcard.example.com"})
    assert resp.status_code == 200 and "save_card" in resp.text


@pytest.mark.parametrize(
    "over, message",
    [
        ({"aud": "other"}, "이 앱에"),
        ({"iss": "https://evil"}, "이 앱에"),
        ({"exp": 1}, "만료"),
        ({"nonce": "x"}, "일치하지"),
        ({"email_verified": False}, "확인되지"),
    ],
)
def test_check_claims_rejects(over, message):
    with pytest.raises(signin.SignInError, match=message):
        signin.check_claims({**_claims("n"), **over}, CLIENT_ID, "n", {"me@example.com"})


def test_google_settings_required():
    with pytest.raises(ValueError, match="FLASHCARD_GOOGLE_CLIENT_ID"):
        Settings(_env_file=None, auth="google")
    with pytest.raises(ValueError, match="32"):
        Settings(
            _env_file=None,
            auth="google",
            google_client_id="x",
            google_client_secret="y",
            allowed_emails="a@b.c",
            session_secret="short",
        )


def test_exchange_code_decodes_id_token(monkeypatch):
    import base64
    import json

    import httpx

    payload = base64.urlsafe_b64encode(json.dumps({"email": "me@example.com"}).encode()).rstrip(b"=")
    token = f"h.{payload.decode()}.sig"
    sent = {}

    def fake_post(url, data, timeout):
        sent.update(url=url, grant=data["grant_type"], redirect=data["redirect_uri"])
        return httpx.Response(200, json={"id_token": token})

    monkeypatch.setattr(httpx, "post", fake_post)
    claims = signin.exchange_code("c", CLIENT_ID, "s", "https://x/auth/callback")
    assert claims == {"email": "me@example.com"}
    assert sent == {
        "url": signin.TOKEN_URL,
        "grant": "authorization_code",
        "redirect": "https://x/auth/callback",
    }
    monkeypatch.setattr(httpx, "post", lambda url, data, timeout: httpx.Response(400, json={}))
    with pytest.raises(signin.SignInError, match="거부"):
        signin.exchange_code("c", CLIENT_ID, "s", "https://x/auth/callback")
