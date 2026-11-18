"""Card repository. Each public method is one transaction."""

import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, and_, case, delete, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from flashcard import srs
from flashcard.db import cards

DECK_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
DEFAULT_DECKS = {"english": "English", "it": "IT", "psychology": "Psychology"}
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
            return conn.execute(delete(cards).where(cards.c.id == card_id)).rowcount > 0

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
        """All decks with total and due counts; default decks are listed even when empty."""
        now = now_utc()
        stmt = select(
            cards.c.deck,
            func.count().label("total"),
            func.sum(case((cards.c.due_at <= now, 1), else_=0)).label("due"),
        ).group_by(cards.c.deck)
        with self.engine.connect() as conn:
            counts = {r.deck: (int(r.total), int(r.due or 0)) for r in conn.execute(stmt)}
        names = list(DEFAULT_DECKS) + sorted(d for d in counts if d not in DEFAULT_DECKS)
        return [
            {
                "deck": d,
                "label": deck_label(d),
                "total": counts.get(d, (0, 0))[0],
                "due": counts.get(d, (0, 0))[1],
            }
            for d in names
        ]

    def next_due(self, deck: str) -> tuple[Card | None, int]:
        """Oldest due card in the deck and the number of due cards."""
        now = now_utc()
        cond = and_(cards.c.deck == normalize_deck(deck), cards.c.due_at <= now)
        with self.engine.connect() as conn:
            count = conn.execute(select(func.count()).where(cond)).scalar_one()
            row = conn.execute(
                select(cards).where(cond).order_by(cards.c.due_at, cards.c.id).limit(1)
            ).first()
        return (self._row(row) if row else None), int(count)

    def review(self, card_id: int, rating: srs.Rating) -> Card:
        if rating not in srs.RATINGS:
            raise CardError("rating must be again, hard or good")
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
        updated = self.get(card_id)
        assert updated is not None
        return updated
