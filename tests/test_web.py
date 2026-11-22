import base64
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from flashcard.config import Settings
from flashcard.db import cards
from flashcard.web import create_app

from .conftest import CODEX_TOKEN, TOKEN

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
    card_id = r.headers["location"].split("/")[2].split("?")[0]
    page = client.get("/d/english/review", headers=AUTH).text
    assert "출근길에" in page and "답 보기" in page and "on my way to work" not in page
    page = client.get(f"/d/english/review?show={card_id}", headers=AUTH).text
    assert "on my way to work" in page
    assert all(label in page for label in ("다시", "어려움", "알맞음", "쉬움"))
    r = client.post(f"/c/{card_id}/review", headers=AUTH, data={"rating": "good"}, follow_redirects=False)
    assert r.status_code == 303
    assert "학습할 카드가 없습니다" in client.get("/d/english/review", headers=AUTH).text
    assert "on my way to work" in client.get("/browse?q=WAY", headers=AUTH).text
    card_page = client.get(f"/c/{card_id}", headers=AUTH).text
    assert "알맞음" in card_page  # review history row
    assert "1" in client.get("/stats", headers=AUTH).text


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
        Settings(_env_file=None, host="0.0.0.0", sqlite_path="x.db")
    with pytest.raises(ValueError):
        Settings(_env_file=None, mcp_tokens="claude=short")


def test_db_choice(tmp_path):
    assert Settings(_env_file=None, sqlite_path=str(tmp_path / "a.db")).sqlite_url.endswith("/a.db")
    with pytest.raises(ValueError, match="FLASHCARD_DB_USER"):
        Settings(_env_file=None, db="oracle")
    with pytest.raises(ValueError, match="FLASHCARD_DB=oracle"):
        Settings(_env_file=None, db_dsn="mydb_low")
    with pytest.raises(ValueError):
        Settings(_env_file=None, db="postgres")


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


def test_english_review_is_korean_first(client):
    r = client.post(
        "/d/english/add",
        headers=AUTH,
        data={"front": "I'll hang up now.", "back": "이제 전화 끊을게요."},
        follow_redirects=False,
    )
    cid = r.headers["location"].split("/")[2].split("?")[0]
    page = client.get("/d/english/review", headers=AUTH).text
    assert "영어로 말해 보세요" in page and "이제 전화 끊을게요." in page
    assert "I&#39;ll hang up now." not in page
    page = client.get(f"/d/english/review?show={cid}", headers=AUTH).text
    assert page.index("이제 전화 끊을게요.") < page.index("I&#39;ll hang up now.")
    r = client.post(
        "/d/it/add", headers=AUTH, data={"front": "IoC", "back": "제어의 역전"}, follow_redirects=False
    )
    iid = r.headers["location"].split("/")[2].split("?")[0]
    it_page = client.get(f"/d/it/review?show={iid}", headers=AUTH).text
    assert it_page.index("IoC") < it_page.index("제어의 역전")
    assert "영어로 말해 보세요" not in it_page


def test_tabs_and_queue_order(client, store):
    for path, tab in (("/", "덱"), ("/browse", "찾아보기"), ("/stats", "통계")):
        page = client.get(path, headers=AUTH).text
        assert page.count('aria-current="page"') == 1 and "mf-tabbar" in page and tab in page
    assert "mf-tabbar" not in client.get("/d/english", headers=AUTH).text  # drill-down has no tab bar
    old = store.add("it", "old", "x")[0]
    new = store.add("it", "new", "y")[0]
    store.review(old.id, "good")
    store.review(new.id, "again")
    store.add("it", "fresh", "z")
    assert store.next_due("it")[0].front == "fresh"  # relearning card still waiting 10 min
    with store.engine.begin() as conn:
        conn.execute(update(cards).where(cards.c.id == new.id).values(due_at=datetime(2000, 1, 1)))
    assert store.next_due("it")[0].id == new.id  # relearning comes before new cards
    counts = store.deck_counts("it")
    assert (counts["new"], counts["learn"], counts["review"]) == (1, 1, 0)


def test_connect_page_hides_token_urls(client):
    page = client.get("/connect", headers=AUTH)
    assert page.status_code == 200
    assert "claude, codex" in page.text
    assert TOKEN not in page.text and CODEX_TOKEN not in page.text
    assert client.get("/connect").status_code == 401
    assert 'href="/connect"' in client.get("/", headers=AUTH).text
    assert client.get("/connect/claude-ai", headers=AUTH).status_code == 404  # no OAuth URL to show
