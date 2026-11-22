import base64
import hashlib
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from flashcard import auth as signin
from flashcard.config import Settings
from flashcard.db import cards, oauth_tokens
from flashcard.oauth import client_label
from flashcard.web import create_app

from .conftest import TOKEN

CLIENT_ID = "cid.apps.googleusercontent.com"
REDIRECT = "https://client.example/callback"
HOST = "flashcard.example.com"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()


def _settings(tmp_path, **over):
    base = dict(
        _env_file=None,
        host="0.0.0.0",
        sqlite_path=str(tmp_path / "unused.db"),
        auth="google",
        mcp_auth="google",
        google_client_id=CLIENT_ID,
        google_client_secret="secret",
        allowed_emails="me@example.com",
        session_secret="s" * 40,
        public_host=HOST,
    )
    return Settings(**{**base, **over})


@pytest.fixture
def app(tmp_path, engine):
    return create_app(_settings(tmp_path, mcp_tokens=f"claude={TOKEN}"), engine)


@pytest.fixture
def client(app):
    with TestClient(app, base_url=f"https://{HOST}", follow_redirects=False) as c:
        yield c


def _register(client, name="Claude"):
    resp = client.post(
        "/register",
        json={"redirect_uris": [REDIRECT], "client_name": name, "token_endpoint_auth_method": "none"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["client_id"]


def _authorize(client, monkeypatch, client_id, email="me@example.com"):
    resp = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
            "state": "client-state",
        },
    )
    assert resp.status_code == 302, resp.text
    google = urlsplit(resp.headers["location"])
    assert google.netloc == "accounts.google.com"
    query = parse_qs(google.query)
    assert query["redirect_uri"] == [f"https://{HOST}/auth/callback"]
    nonce = query["nonce"][0]
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": time.time() + 300,
        "nonce": nonce,
        "email": email,
        "email_verified": True,
    }
    monkeypatch.setattr(signin, "exchange_code", lambda *a: claims)
    return client.get("/auth/callback", params={"code": "google-code", "state": query["state"][0]})


def _tokens(client, monkeypatch, client_id):
    back = _authorize(client, monkeypatch, client_id)
    assert back.status_code == 302
    target = urlsplit(back.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == REDIRECT
    query = parse_qs(target.query)
    assert query["state"] == ["client-state"]
    code = query["code"][0]
    resp = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": client_id,
            "code_verifier": VERIFIER,
        },
    )
    assert resp.status_code == 200, resp.text
    return code, resp.json()


def _rpc(client, method, params=None, bearer=None, path="/mcp"):
    headers = {**MCP_HEADERS, "Host": HOST}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post(path, json=body, headers=headers)


def test_metadata_and_unauthenticated(client):
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["issuer"].rstrip("/") == f"https://{HOST}"
    assert meta["authorization_endpoint"] == f"https://{HOST}/authorize"
    assert "S256" in meta["code_challenge_methods_supported"]
    resource = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert resource["resource"] == f"https://{HOST}/mcp"
    resp = _rpc(client, "tools/list")
    assert resp.status_code == 401
    assert "resource_metadata" in resp.headers["www-authenticate"]
    assert _rpc(client, "tools/list", bearer="made-up").status_code == 401


def test_full_flow_save_card_records_client_and_email(client, monkeypatch, engine):
    client_id = _register(client, "Claude Code")
    _, tokens = _tokens(client, monkeypatch, client_id)
    assert tokens["token_type"].lower() == "bearer" and tokens["refresh_token"]
    listed = _rpc(client, "tools/list", bearer=tokens["access_token"])
    assert listed.status_code == 200 and "save_card" in listed.text
    saved = _rpc(
        client,
        "tools/call",
        {"name": "save_card", "arguments": {"deck": "it", "front": "PKCE", "back": "code challenge"}},
        bearer=tokens["access_token"],
    )
    assert '"created"' in saved.text
    with engine.connect() as conn:
        assert conn.execute(select(cards.c.created_by)).scalar_one() == "mcp:claude-code/me@example.com"
        stored = conn.execute(select(oauth_tokens.c.token_hash)).scalars().all()
    assert tokens["access_token"] not in stored and len(stored) == 2  # only hashes kept


def test_code_is_single_use_and_needs_pkce(client, monkeypatch):
    client_id = _register(client)
    code, _ = _tokens(client, monkeypatch, client_id)
    again = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": client_id,
            "code_verifier": VERIFIER,
        },
    )
    assert again.status_code == 400
    back = _authorize(client, monkeypatch, client_id)
    fresh = parse_qs(urlsplit(back.headers["location"]).query)["code"][0]
    wrong = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": fresh,
            "redirect_uri": REDIRECT,
            "client_id": client_id,
            "code_verifier": "w" * 64,
        },
    )
    assert wrong.status_code == 400


def test_refresh_rotates_and_revoke(client, monkeypatch):
    client_id = _register(client)
    _, first = _tokens(client, monkeypatch, client_id)
    resp = client.post(
        "/token",
        data={"grant_type": "refresh_token", "refresh_token": first["refresh_token"], "client_id": client_id},
    )
    assert resp.status_code == 200, resp.text
    second = resp.json()
    assert _rpc(client, "tools/list", bearer=first["access_token"]).status_code == 401
    assert _rpc(client, "tools/list", bearer=second["access_token"]).status_code == 200
    old_refresh = client.post(
        "/token",
        data={"grant_type": "refresh_token", "refresh_token": first["refresh_token"], "client_id": client_id},
    )
    assert old_refresh.status_code == 400
    revoked = client.post(
        "/revoke", data={"token": second["access_token"], "client_id": client_id, "client_secret": ""}
    )
    assert revoked.status_code == 200
    assert _rpc(client, "tools/list", bearer=second["access_token"]).status_code == 401


def test_removed_from_allow_list_blocks_existing_token(app, client, monkeypatch):
    client_id = _register(client)
    _, tokens = _tokens(client, monkeypatch, client_id)
    app.state.oauth.allowed.discard("me@example.com")
    assert _rpc(client, "tools/list", bearer=tokens["access_token"]).status_code == 401


def test_not_allowed_email_gets_no_code(client, monkeypatch):
    client_id = _register(client)
    back = _authorize(client, monkeypatch, client_id, email="stranger@example.com")
    assert back.status_code == 403 and "stranger@example.com" in back.text


def test_path_token_still_works_in_google_mode(client):
    resp = _rpc(client, "tools/list", path=f"/m/{TOKEN}/mcp")
    assert resp.status_code == 200 and "save_card" in resp.text


def test_web_routes_unaffected_by_root_mount(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/").status_code == 303  # web sign-in redirect, not the MCP app


def test_mcp_google_needs_web_google(tmp_path):
    with pytest.raises(ValueError, match="FLASHCARD_AUTH=google"):
        Settings(_env_file=None, mcp_auth="google")


def test_google_mcp_without_path_tokens_is_allowed(tmp_path):
    assert _settings(tmp_path).client_tokens == {}


def test_client_label():
    assert client_label("Claude Code") == "claude-code"
    assert client_label(None) == "client"
    assert client_label("ChatGPT (connector) for flashcard!!") == "chatgpt-connector-fo"


def test_registration_and_pending_caps(app, client, monkeypatch):
    import flashcard.oauth as oauth_mod

    monkeypatch.setattr(oauth_mod, "MAX_CLIENTS", 1)
    _register(client)
    refused = client.post(
        "/register", json={"redirect_uris": [REDIRECT], "token_endpoint_auth_method": "none"}
    )
    assert refused.status_code == 400
    monkeypatch.setattr(oauth_mod, "MAX_PENDING", 0)
    with app.state.oauth.engine.connect() as conn:
        from flashcard.db import oauth_clients

        client_id = conn.execute(select(oauth_clients.c.client_id)).scalar_one()
    resp = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
            "state": "s",
        },
    )
    assert resp.status_code == 302 and "temporarily_unavailable" in resp.headers["location"]


def test_connect_page_shows_mcp_url_after_sign_in(client, monkeypatch):
    assert client.get("/connect").status_code == 303  # sign in first
    start = parse_qs(
        urlsplit(client.get("/auth/login", params={"next": "/connect"}).headers["location"]).query
    )
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": time.time() + 300,
        "nonce": start["nonce"][0],
        "email": "me@example.com",
        "email_verified": True,
    }
    monkeypatch.setattr(signin, "exchange_code", lambda *a: claims)
    back = client.get("/auth/callback", params={"code": "c", "state": start["state"][0]})
    assert back.headers["location"] == "/connect"
    page = client.get("/connect")
    assert page.status_code == 200
    assert f'value="https://{HOST}/mcp"' in page.text
    assert TOKEN not in page.text and "claude" in page.text
    detail = client.get("/connect/claude-code")
    assert detail.status_code == 200
    assert f"claude mcp add --transport http flashcard https://{HOST}/mcp" in detail.text
    assert client.get("/connect/nope").status_code == 404
