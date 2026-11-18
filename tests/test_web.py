import base64

import pytest
from fastapi.testclient import TestClient

from flashcard.config import Settings
from flashcard.web import create_app

from .conftest import TOKEN

AUTH = {"Authorization": "Basic " + base64.b64encode(b"me:pw").decode()}


@pytest.fixture
def client(settings, engine):
    with TestClient(create_app(settings, engine), base_url="http://flashcard.example.com") as c:
        yield c


def test_auth_required(client):
    assert client.get("/").status_code == 401
    assert (
        client.get("/", headers={"Authorization": "Basic " + base64.b64encode(b"me:x").decode()}).status_code
        == 401
    )
    assert client.get("/healthz").status_code == 200
    assert client.get("/", headers=AUTH).status_code == 200


def test_add_review_flow(client):
    r = client.post(
        "/d/english/add",
        headers=AUTH,
        data={"front": "on my way to work", "back": "출근길에"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/c/")
    page = client.get("/d/english/review", headers=AUTH)
    assert "on my way to work" in page.text and "앎" in page.text
    card_id = r.headers["location"].split("/")[2].split("?")[0]
    r = client.post(f"/c/{card_id}/review", headers=AUTH, data={"rating": "good"}, follow_redirects=False)
    assert r.status_code == 303
    assert "복습할 카드가 없습니다" in client.get("/d/english/review", headers=AUTH).text
    assert "on my way to work" in client.get("/d/english?q=WAY", headers=AUTH).text


def test_new_deck_and_errors(client):
    r = client.post(
        "/d/english/add",
        headers=AUTH,
        data={"front": "VPC", "back": "가상 네트워크", "new_deck": "aws-saa"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "aws-saa" in client.get("/", headers=AUTH).text
    r = client.post("/d/english/add", headers=AUTH, data={"front": "", "back": "x"}, follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert client.get("/d/BAD!!/review", headers=AUTH).status_code == 404


def test_cross_origin_post_rejected(client):
    r = client.post(
        "/d/english/add", headers={**AUTH, "Origin": "https://evil.example"}, data={"front": "a", "back": "b"}
    )
    assert r.status_code == 403


def test_html_escaped(client):
    client.post("/d/it/add", headers=AUTH, data={"front": "<script>x</script>", "back": "b"})
    assert "<script>x</script>" not in client.get("/d/it", headers=AUTH).text


def test_exposed_bind_requires_auth(tmp_path):
    with pytest.raises(ValueError):
        Settings(_env_file=None, host="0.0.0.0", database_url="sqlite:///x.db")
    with pytest.raises(ValueError):
        Settings(_env_file=None, mcp_tokens="claude=short")


MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _rpc(client, method, params=None, path=f"/m/{TOKEN}/mcp", host="flashcard.example.com"):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post(path, json=body, headers={**MCP_HEADERS, "Host": host})


def test_mcp_tools(client):
    init = _rpc(
        client,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
    )
    assert init.status_code == 200, init.text
    names = {t["name"] for t in _rpc(client, "tools/list").json()["result"]["tools"]}
    assert names == {"list_decks", "search_cards", "save_card", "update_card"}
    saved = _rpc(
        client,
        "tools/call",
        {
            "name": "save_card",
            "arguments": {
                "deck": "english",
                "front": "I'll hang up now",
                "back": "이제 끊을게",
                "note": "call it off",
            },
        },
    )
    result = saved.json()["result"]
    assert not result.get("isError") and "created" in result["content"][0]["text"]
    dup = _rpc(
        client,
        "tools/call",
        {"name": "save_card", "arguments": {"deck": "english", "front": "i'll hang up NOW", "back": "x"}},
    )
    assert '"exists"' in dup.json()["result"]["content"][0]["text"]
    found = _rpc(client, "tools/call", {"name": "search_cards", "arguments": {"query": "hang up"}})
    assert "이제 끊을게" in found.json()["result"]["content"][0]["text"]


def test_mcp_wrong_token_or_host(client):
    assert _rpc(client, "tools/list", path="/m/wrong/mcp").status_code == 404
    assert _rpc(client, "tools/list", host="evil.example").status_code == 421


def _saved_card(response):
    import json

    return json.loads(response.json()["result"]["content"][0]["text"])["card"]


def test_saved_by_client_label(client):
    from .conftest import CODEX_TOKEN

    a = _rpc(
        client,
        "tools/call",
        {"name": "save_card", "arguments": {"deck": "it", "front": "AOP", "back": "관점 지향"}},
    )
    assert _saved_card(a)["created_by"] == "mcp:claude"
    b = _rpc(
        client,
        "tools/call",
        {"name": "save_card", "arguments": {"deck": "it", "front": "IoC", "back": "제어의 역전"}},
        path=f"/m/{CODEX_TOKEN}/mcp",
    )
    card = _saved_card(b)
    assert card["created_by"] == "mcp:codex"
    u = _rpc(
        client, "tools/call", {"name": "update_card", "arguments": {"card_id": card["id"], "note": "Spring"}}
    )
    assert _saved_card(u)["updated_by"] == "mcp:claude" and _saved_card(u)["created_by"] == "mcp:codex"
    r = client.post(
        "/d/it/add", headers=AUTH, data={"front": "DI", "back": "의존성 주입"}, follow_redirects=False
    )
    page = client.get(r.headers["location"], headers=AUTH).text
    assert "web:me" in page


def test_token_config_validation():
    with pytest.raises(ValueError):
        Settings(_env_file=None, mcp_tokens="claude")  # no token
    with pytest.raises(ValueError):
        Settings(_env_file=None, mcp_tokens=f"claude={'a' * 40},codex={'a' * 40}")  # duplicate token
    with pytest.raises(ValueError):
        Settings(_env_file=None, mcp_tokens=f"Claude!={'a' * 40}")  # bad label
