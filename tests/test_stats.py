from datetime import date, datetime

from flashcard.stats import local_day, streak, summarize, utc_start_of


def test_local_day_boundary():
    # 15:00 UTC = 00:00 KST next day
    assert local_day(datetime(2026, 1, 1, 14, 59)) == date(2026, 1, 1)
    assert local_day(datetime(2026, 1, 1, 15, 0)) == date(2026, 1, 2)
    assert utc_start_of(date(2026, 1, 2)) == datetime(2026, 1, 1, 15, 0)


def test_streak():
    t = date(2026, 1, 10)
    assert streak({date(2026, 1, 10), date(2026, 1, 9), date(2026, 1, 7)}, t) == 2
    assert streak({date(2026, 1, 9), date(2026, 1, 8)}, t) == 2  # today not yet studied
    assert streak({date(2026, 1, 7)}, t) == 0


def test_summarize():
    now = datetime(2026, 1, 10, 3, 0)  # 12:00 KST, Jan 10
    rows = [
        {"deck": "english", "rating": "again", "reviewed_at": datetime(2026, 1, 10, 1, 0)},
        {"deck": "english", "rating": "good", "reviewed_at": datetime(2026, 1, 10, 1, 5)},
        {"deck": "it", "rating": "easy", "reviewed_at": datetime(2026, 1, 9, 1, 0)},
    ]
    s = summarize(rows, now, window=7)
    assert (s.today, s.today_pass_rate, s.streak, s.total, s.pass_rate) == (2, 50, 2, 3, 67)
    assert len(s.days) == 7 and s.days[-1]["count"] == 2 and s.max_count == 2
    assert s.decks[0] == {"deck": "english", "count": 2, "pass_rate": 50}
