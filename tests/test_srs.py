from datetime import datetime, timedelta

from flashcard.srs import MIN_EASE, State, schedule

NOW = datetime(2026, 1, 1)


def test_good_progression():
    s, due = schedule(State(), "good", NOW)
    assert (s.reps, s.interval_days, due) == (1, 1.0, NOW + timedelta(days=1))
    s, due = schedule(s, "good", NOW)
    assert (s.reps, s.interval_days) == (2, 3.0)
    s, _ = schedule(s, "good", NOW)
    assert s.interval_days == 7.5 and s.reps == 3


def test_again_resets_and_lowers_ease():
    s, due = schedule(State(2.5, 10, 4, 0), "again", NOW)
    assert (s.reps, s.lapses, s.interval_days) == (0, 1, 0.0)
    assert s.ease == 2.3 and due == NOW + timedelta(minutes=10)


def test_hard_and_ease_floor():
    s, due = schedule(State(1.35, 5, 3, 0), "hard", NOW)
    assert s.ease == MIN_EASE and s.interval_days == 6.0 and s.reps == 3
    assert due == NOW + timedelta(days=6)
