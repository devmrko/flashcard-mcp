import pytest

from flashcard.store import CardError


def test_add_dedup_and_search(store):
    card, created = store.add("English", "Bounce back", "회복하다", "I want to bounce back.", source="mcp")
    assert created and card.deck == "english" and card.source == "mcp"
    again, created = store.add("english", "  bounce   BACK ", "other")
    assert not created and again.id == card.id and again.back == "회복하다"
    assert [c.id for c in store.search("BOUNCE")] == [card.id]
    assert [c.id for c in store.search("회복")] == [card.id]
    assert store.search("bounce", "it") == []
    assert store.search("100%") == []  # LIKE wildcards are escaped


def test_validation(store):
    with pytest.raises(CardError):
        store.add("bad deck!", "a", "b")
    with pytest.raises(CardError):
        store.add("it", "", "b")
    with pytest.raises(CardError):
        store.add("it", "a", "b" * 2001)


def test_edit_and_conflict(store):
    a, _ = store.add("it", "AOP", "관점 지향 프로그래밍")
    b, _ = store.add("it", "IoC", "제어의 역전", example="Spring container")
    edited = store.edit(b.id, example="", note="Spring")
    assert edited.example is None and edited.note == "Spring" and edited.back == "제어의 역전"
    with pytest.raises(CardError):
        store.edit(b.id, front="aop")
    with pytest.raises(CardError):
        store.edit(999, back="x")
    assert store.remove(a.id) and store.get(a.id) is None


def test_decks_due_and_review(store):
    card, _ = store.add("psychology", "Dopamine", "동기 신경전달물질")
    decks = {d["deck"]: d for d in store.decks()}
    assert list(decks)[:3] == ["english", "it", "psychology"]
    assert decks["psychology"] == {
        "deck": "psychology",
        "label": "Psychology",
        "total": 1,
        "new": 1,
        "learn": 0,
        "review": 0,
        "due": 1,
    }
    nxt, due = store.next_due("psychology")
    assert nxt.id == card.id and due == 1
    reviewed = store.review(card.id, "good")
    assert reviewed.reps == 1
    assert store.next_due("psychology") == (None, 0)
    with pytest.raises(CardError):
        store.review(card.id, "perfect")


def test_review_history(store):
    card, _ = store.add("english", "I'll hang up now.", "이제 전화 끊을게요.")
    store.review(card.id, "again", by="web:me")
    store.review(card.id, "good", by="web:me")
    hist = store.history(card.id)
    assert [h["rating"] for h in hist] == ["good", "again"]
    assert hist[0]["reviewed_by"] == "web:me" and hist[0]["interval_after"] == 1.0
    with pytest.raises(CardError):
        store.review(card.id, "bad")
    assert len(store.history(card.id)) == 2  # invalid rating writes nothing
    store.remove(card.id)
    assert store.history(card.id) == []
