"""Practice statistics from the fc_review log. Days are counted in the user's local time (Asia/Seoul)."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

LOCAL = ZoneInfo("Asia/Seoul")
PASS = {"hard", "good", "easy"}


def local_day(utc_naive: datetime) -> date:
    return utc_naive.replace(tzinfo=UTC).astimezone(LOCAL).date()


def local_today(now_utc: datetime) -> date:
    return local_day(now_utc)


def utc_start_of(day: date) -> datetime:
    """Naive UTC instant at local midnight of `day`."""
    return datetime(day.year, day.month, day.day, tzinfo=LOCAL).astimezone(UTC).replace(tzinfo=None)


@dataclass
class Summary:
    today: int
    today_pass_rate: int | None
    streak: int
    days: list[dict[str, Any]]  # last N days, oldest first: {day, count, label}
    max_count: int
    decks: list[dict[str, Any]]  # per deck over the window: {deck, count, pass_rate}
    total: int
    pass_rate: int | None


def _rate(passed: int, total: int) -> int | None:
    return round(100 * passed / total) if total else None


def streak(days_with_reviews: set[date], today: date) -> int:
    """Consecutive days with at least one review, ending today (or yesterday if today has none yet)."""
    day = today if today in days_with_reviews else today - timedelta(days=1)
    n = 0
    while day in days_with_reviews:
        n += 1
        day -= timedelta(days=1)
    return n


def summarize(rows: list[dict[str, Any]], now_utc: datetime, window: int = 30) -> Summary:
    """rows: review log since at least `window` days ago (deck, rating, reviewed_at)."""
    today = local_today(now_utc)
    first = today - timedelta(days=window - 1)
    per_day: Counter[date] = Counter()
    today_pass = 0
    deck_total: Counter[str] = Counter()
    deck_pass: defaultdict[str, int] = defaultdict(int)
    seen_days: set[date] = set()
    for r in rows:
        day = local_day(r["reviewed_at"])
        seen_days.add(day)
        if day < first:
            continue
        per_day[day] += 1
        deck_total[r["deck"]] += 1
        passed = r["rating"] in PASS
        deck_pass[r["deck"]] += passed
        if day == today:
            today_pass += passed
    days = []
    for i in range(window):
        d = first + timedelta(days=i)
        days.append({"day": d, "count": per_day[d], "label": f"{d.month}/{d.day}"})
    total = sum(deck_total.values())
    decks = [
        {"deck": k, "count": v, "pass_rate": _rate(deck_pass[k], v)} for k, v in deck_total.most_common()
    ]
    return Summary(
        today=per_day[today],
        today_pass_rate=_rate(today_pass, per_day[today]),
        streak=streak(seen_days, today),
        days=days,
        max_count=max((d["count"] for d in days), default=0),
        decks=decks,
        total=total,
        pass_rate=_rate(sum(deck_pass.values()), total),
    )


def to_local(utc_naive: datetime) -> datetime:
    return utc_naive.replace(tzinfo=UTC).astimezone(LOCAL)
