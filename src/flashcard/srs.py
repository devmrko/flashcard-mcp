"""Simplified SM-2 with three answers. Pure functions; times are naive UTC."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

Rating = Literal["again", "hard", "good"]
RATINGS: tuple[Rating, ...] = ("again", "hard", "good")
MIN_EASE = 1.3


@dataclass(frozen=True)
class State:
    ease: float = 2.5
    interval_days: float = 0.0
    reps: int = 0
    lapses: int = 0


def schedule(state: State, rating: Rating, now: datetime) -> tuple[State, datetime]:
    if rating == "again":
        new = State(max(MIN_EASE, state.ease - 0.2), 0.0, 0, state.lapses + 1)
        return new, now + timedelta(minutes=10)
    if rating == "hard":
        interval = max(1.0, state.interval_days * 1.2)
        new = State(max(MIN_EASE, state.ease - 0.15), interval, state.reps, state.lapses)
        return new, now + timedelta(days=interval)
    if state.reps == 0:
        interval = 1.0
    elif state.reps == 1:
        interval = 3.0
    else:
        interval = max(1.0, state.interval_days * state.ease)
    new = State(state.ease, round(interval, 2), state.reps + 1, state.lapses)
    return new, now + timedelta(days=new.interval_days)
