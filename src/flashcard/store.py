"""Card repository. Each public method is one transaction."""

import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, and_, case, delete, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from flashcard import srs
from flashcard.db import cards, reviews

DECK_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
DEFAULT_DECKS = {"english": "English", "it": "IT", "psychology": "Psychology"}
# Decks reviewed back-first: show the Korean meaning, recall the English expression (production practice).
REVERSED_DECKS = {"english"}
LIMITS = {"front": 500, "back": 2000, "example": 2000, "note": 2000}


class CardError(ValueError):
    """Input problem safe to show to the user / the MCP client."""


@dataclass
class Card:
    id: int
    deck: str
    front: str
    back: str
    example: str | None
    note: str | None
    source: str
    created_by: str | None
    updated_by: str | None
    ease: float
    interval_days: float
    reps: int
    lapses: int
    due_at: datetime
    created_at: datetime
    updated_at: datetime

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        for key in ("due_at", "created_at", "updated_at"):
            d[key] = d[key].isoformat(timespec="seconds") + "Z"
        return d


def now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def normalize_deck(deck: str) -> str:
    value = deck.strip().lower().replace(" ", "-")
    if not DECK_RE.match(value):
        raise CardError("deck must be 1-40 chars of a-z, 0-9, '-'")
    return value


def front_key(front: str) -> str:
    return " ".join(front.lower().split())


def deck_label(deck: str) -> str:
    return DEFAULT_DECKS.get(deck, deck)


def _clean(field: str, value: str | None, required: bool = False) -> str | None:
    text = (value or "").strip()
    if not text:
        if required:
            raise CardError(f"{field} is required")
        return None
    if len(text) > LIMITS[field]:
        raise CardError(f"{field} is longer than {LIMITS[field]} characters")
    return text


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class CardStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def _row(self, row: Any) -> Card:
        return Card(**{k: row._mapping[k] for k in Card.__dataclass_fields__})

    def get(self, card_id: int) -> Card | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(cards).where(cards.c.id == card_id)).first()
        return self._row(row) if row else None

    def find_front(self, deck: str, front: str) -> Card | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(cards).where(
                    cards.c.deck == normalize_deck(deck), cards.c.front_key == front_key(front)
                )
            ).first()
        return self._row(row) if row else None

    def add(
        self,
        deck: str,
        front: str,
        back: str,
        example: str | None = None,
        note: str | None = None,
        source: str = "web",
        by: str | None = None,
    ) -> tuple[Card, bool]:
        """Returns (card, created). An existing card with the same front in the deck is returned unchanged."""
        deck = normalize_deck(deck)
        front_v = _clean("front", front, required=True)
        assert front_v is not None
        values = {
            "deck": deck,
            "front": front_v,
            "front_key": front_key(front_v),
            "back": _clean("back", back, required=True),
            "example": _clean("example", example),
            "note": _clean("note", note),
            "source": source,
            "created_by": by,
            "updated_by": by,
        }
        existing = self.find_front(deck, front_v)
        if existing:
            return existing, False
        ts = now_utc()
        state = srs.State()
        try:
            with self.engine.begin() as conn:
                result = conn.execute(
                    insert(cards)
                    .values(
                        **values,
                        ease=state.ease,
                        interval_days=state.interval_days,
                        reps=state.reps,
                        lapses=state.lapses,
                        due_at=ts,
                        created_at=ts,
                        updated_at=ts,
                    )
                    .returning(cards.c.id)
                )
                new_id = result.scalar_one()
        except IntegrityError:  # concurrent insert of the same front
            existing = self.find_front(deck, front_v)
            if existing:
                return existing, False
            raise
        card = self.get(new_id)
        assert card is not None
        return card, True

    def edit(self, card_id: int, by: str | None = None, **fields: str | None) -> Card:
        """Update given fields; None means 'leave as is', empty string clears optional fields."""
        current = self.get(card_id)
        if current is None:
            raise CardError(f"card {card_id} not found")
        values: dict[str, Any] = {}
        if fields.get("deck") is not None:
            values["deck"] = normalize_deck(fields["deck"] or "")
        for name in ("front", "back"):
            if fields.get(name) is not None:
                values[name] = _clean(name, fields[name], required=True)
        for name in ("example", "note"):
            if fields.get(name) is not None:
                values[name] = _clean(name, fields[name])
        if "front" in values:
            values["front_key"] = front_key(values["front"])
        if not values:
            return current
        values["updated_at"] = now_utc()
        values["updated_by"] = by
        try:
            with self.engine.begin() as conn:
                conn.execute(update(cards).where(cards.c.id == card_id).values(**values))
        except IntegrityError as exc:
            raise CardError("another card in that deck already has this front") from exc
        card = self.get(card_id)
        assert card is not None
        return card

    def remove(self, card_id: int) -> bool:
        with self.engine.begin() as conn:
            conn.execute(delete(reviews).where(reviews.c.card_id == card_id))
            return conn.execute(delete(cards).where(cards.c.id == card_id)).rowcount > 0

    def history(self, card_id: int, limit: int = 50) -> list[dict[str, Any]]:
        """Most recent review answers for one card."""
        stmt = (
            select(reviews.c.rating, reviews.c.reviewed_at, reviews.c.interval_after, reviews.c.reviewed_by)
            .where(reviews.c.card_id == card_id)
            .order_by(reviews.c.reviewed_at.desc(), reviews.c.id.desc())
            .limit(limit)
        )
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(stmt)]

    def search(self, query: str = "", deck: str | None = None, limit: int = 20) -> list[Card]:
        stmt = select(cards)
        if deck:
            stmt = stmt.where(cards.c.deck == normalize_deck(deck))
        text = " ".join(query.lower().split())
        if text:
            pattern = f"%{_escape_like(text)}%"
            stmt = stmt.where(
                or_(
                    cards.c.front_key.like(pattern, escape="\\"),
                    func.lower(cards.c.back).like(pattern, escape="\\"),
                    func.lower(cards.c.example).like(pattern, escape="\\"),
                )
            )
        stmt = stmt.order_by(cards.c.created_at.desc(), cards.c.id.desc()).limit(max(1, min(limit, 200)))
        with self.engine.connect() as conn:
            return [self._row(r) for r in conn.execute(stmt)]

    def decks(self) -> list[dict[str, Any]]:
        """All decks with total count and due counts split Anki-style into new / learn / review.
        Default decks are listed even when empty."""
        now = now_utc()
        is_due = cards.c.due_at <= now
        is_review = cards.c.interval_days > 0
        is_learn = and_(cards.c.interval_days <= 0, cards.c.lapses > 0)
        is_new = and_(cards.c.interval_days <= 0, cards.c.lapses == 0)

        def due_sum(cond: Any, label: str) -> Any:
            return func.sum(case((and_(is_due, cond), 1), else_=0)).label(label)

        stmt = select(
            cards.c.deck,
            func.count().label("total"),
            due_sum(is_new, "new"),
            due_sum(is_learn, "learn"),
            due_sum(is_review, "review"),
        ).group_by(cards.c.deck)
        with self.engine.connect() as conn:
            rows = {r.deck: r for r in conn.execute(stmt)}
        names = list(DEFAULT_DECKS) + sorted(d for d in rows if d not in DEFAULT_DECKS)
        out = []
        for d in names:
            r = rows.get(d)
            counts = {k: int(getattr(r, k) or 0) if r else 0 for k in ("total", "new", "learn", "review")}
            counts["due"] = counts["new"] + counts["learn"] + counts["review"]
            out.append({"deck": d, "label": deck_label(d), **counts})
        return out

    def deck_counts(self, deck: str) -> dict[str, Any]:
        deck = normalize_deck(deck)
        for d in self.decks():
            if d["deck"] == deck:
                return d
        return {
            "deck": deck,
            "label": deck_label(deck),
            "total": 0,
            "new": 0,
            "learn": 0,
            "review": 0,
            "due": 0,
        }

    def reviews_since(self, since: datetime) -> list[dict[str, Any]]:
        """Review log rows (deck, rating, reviewed_at) since a UTC time, oldest first."""
        stmt = (
            select(reviews.c.deck, reviews.c.rating, reviews.c.reviewed_at)
            .where(reviews.c.reviewed_at >= since)
            .order_by(reviews.c.reviewed_at)
        )
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(stmt)]

    def most_lapsed(self, limit: int = 10) -> list[Card]:
        stmt = (
            select(cards)
            .where(cards.c.lapses > 0)
            .order_by(cards.c.lapses.desc(), cards.c.updated_at.desc())
            .limit(limit)
        )
        with self.engine.connect() as conn:
            return [self._row(r) for r in conn.execute(stmt)]

    def next_due(self, deck: str) -> tuple[Card | None, int]:
        """Next due card and the number of due cards. Order like Anki: relearning cards first,
        then reviews, then new cards; oldest due first within each group."""
        now = now_utc()
        cond = and_(cards.c.deck == normalize_deck(deck), cards.c.due_at <= now)
        group = case(
            (and_(cards.c.interval_days <= 0, cards.c.lapses > 0), 0),
            (cards.c.interval_days > 0, 1),
            else_=2,
        )
        with self.engine.connect() as conn:
            count = conn.execute(select(func.count()).where(cond)).scalar_one()
            row = conn.execute(
                select(cards).where(cond).order_by(group, cards.c.due_at, cards.c.id).limit(1)
            ).first()
        return (self._row(row) if row else None), int(count)

    def review(self, card_id: int, rating: srs.Rating, by: str | None = None) -> Card:
        if rating not in srs.RATINGS:
            raise CardError("rating must be again, hard, good or easy")
        card = self.get(card_id)
        if card is None:
            raise CardError(f"card {card_id} not found")
        ts = now_utc()
        state, due = srs.schedule(
            srs.State(card.ease, card.interval_days, card.reps, card.lapses), rating, ts
        )
        with self.engine.begin() as conn:
            conn.execute(
                update(cards)
                .where(cards.c.id == card_id)
                .values(
                    ease=state.ease,
                    interval_days=state.interval_days,
                    reps=state.reps,
                    lapses=state.lapses,
                    due_at=due,
                    updated_at=ts,
                )
            )
            conn.execute(
                insert(reviews).values(
                    card_id=card_id,
                    deck=card.deck,
                    rating=rating,
                    reviewed_at=ts,
                    interval_before=card.interval_days,
                    interval_after=state.interval_days,
                    ease_after=state.ease,
                    reviewed_by=by,
                )
            )
        updated = self.get(card_id)
        assert updated is not None
        return updated
