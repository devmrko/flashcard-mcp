"""Simplified SM-2 with four answers (Anki-style). Pure functions; times are naive UTC."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

Rating = Literal["again", "hard", "good", "easy"]
RATINGS: tuple[Rating, ...] = ("again", "hard", "good", "easy")
LABELS: dict[str, str] = {"again": "다시", "hard": "어려움", "good": "알맞음", "easy": "쉬움"}
MIN_EASE = 1.3
RELEARN_MINUTES = 10


@dataclass(frozen=True)
class State:
    ease: float = 2.5
    interval_days: float = 0.0
    reps: int = 0
    lapses: int = 0


def schedule(state: State, rating: Rating, now: datetime) -> tuple[State, datetime]:
    if rating == "again":
        new = State(max(MIN_EASE, round(state.ease - 0.2, 2)), 0.0, 0, state.lapses + 1)
        return new, now + timedelta(minutes=RELEARN_MINUTES)
    if rating == "hard":
        interval = max(1.0, round(state.interval_days * 1.2, 2))
        new = State(max(MIN_EASE, round(state.ease - 0.15, 2)), interval, state.reps, state.lapses)
        return new, now + timedelta(days=interval)
    if rating == "good":
        if state.reps == 0:
            interval = 1.0
        elif state.reps == 1:
            interval = 3.0
        else:
            interval = max(state.interval_days + 1, state.interval_days * state.ease)
        new = State(state.ease, round(interval, 2), state.reps + 1, state.lapses)
        return new, now + timedelta(days=new.interval_days)
    # easy
    if state.reps == 0:
        interval = 4.0
    else:
        good = max(state.interval_days + 1, state.interval_days * state.ease, 3.0)
        interval = good * 1.3
    new = State(round(state.ease + 0.15, 2), round(interval, 2), state.reps + 1, state.lapses)
    return new, now + timedelta(days=new.interval_days)


def kind(state: State) -> str:
    """Anki-style queue: new (never scheduled), learn (relearning after 'again'), review."""
    if state.interval_days > 0:
        return "review"
    return "learn" if state.lapses > 0 else "new"
