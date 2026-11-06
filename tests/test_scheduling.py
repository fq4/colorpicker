from datetime import datetime

import pytest

from scheduling import game_week, next_lock


KICKOFFS = {
    1: "2026-09-09T20:20:00-04:00",
    2: "2026-09-17T20:15:00-04:00",
    9: "2026-11-05T20:15:00-05:00",
    10: "2026-11-12T20:15:00-05:00",
    11: "2026-11-19T20:15:00-05:00",
    12: "2026-11-25T20:00:00-05:00",
}


def test_game_week_rolls_over_at_tuesday_three_local():
    before_rollover = datetime.fromisoformat("2026-11-10T02:59:00-05:00")
    at_rollover = datetime.fromisoformat("2026-11-10T03:00:00-05:00")

    assert game_week(before_rollover, KICKOFFS) == 9
    assert game_week(at_rollover, KICKOFFS) == 10


def test_week_twelve_uses_thanksgiving_eve_opener():
    before_opener = datetime.fromisoformat("2026-11-25T19:59:00-05:00")
    after_week_eleven_rollover = datetime.fromisoformat("2026-11-24T03:00:00-05:00")

    assert next_lock(before_opener, KICKOFFS) == (
        12,
        datetime.fromisoformat("2026-11-25T20:00:00-05:00"),
    )
    assert game_week(after_week_eleven_rollover, KICKOFFS) == 12


def test_week_one_wednesday_opener():
    before_opener = datetime.fromisoformat("2026-09-09T20:19:00-04:00")

    assert next_lock(before_opener, KICKOFFS) == (
        1,
        datetime.fromisoformat("2026-09-09T20:20:00-04:00"),
    )
    assert game_week(before_opener, KICKOFFS) == 1


def test_next_lock_skips_kickoff_that_has_started():
    now = datetime.fromisoformat("2026-11-05T20:15:00-05:00")

    assert next_lock(now, KICKOFFS) == (
        10,
        datetime.fromisoformat("2026-11-12T20:15:00-05:00"),
    )


def test_next_lock_errors_when_no_future_kickoff_is_configured():
    now = datetime.fromisoformat("2026-12-01T00:00:00-05:00")

    with pytest.raises(ValueError, match="No future kickoff"):
        next_lock(now, KICKOFFS)
