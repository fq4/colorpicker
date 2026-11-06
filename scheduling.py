"""Pure helpers for selecting fantasy-football weeks from configured kickoffs."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Mapping


def _configured_kickoffs(kickoff_times: Mapping) -> list[tuple[int, datetime]]:
    if not isinstance(kickoff_times, Mapping) or not kickoff_times:
        raise ValueError("kickoff_times must be a non-empty week-to-time mapping.")

    kickoffs = []
    for week, value in kickoff_times.items():
        try:
            week_number = int(week)
            kickoff = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid kickoff entry for Week {week}: {value!r}") from exc
        if kickoff.tzinfo is None or kickoff.utcoffset() is None:
            raise ValueError(f"Kickoff time for Week {week_number} must include a timezone.")
        kickoffs.append((week_number, kickoff))

    kickoffs.sort(key=lambda item: item[0])
    if len({week for week, _ in kickoffs}) != len(kickoffs):
        raise ValueError("kickoff_times contains duplicate week numbers.")
    return kickoffs


def _week_rollover(kickoff: datetime) -> datetime:
    days_until_tuesday = (1 - kickoff.weekday()) % 7
    if days_until_tuesday == 0:
        days_until_tuesday = 7
    rollover_date = kickoff.date() + timedelta(days=days_until_tuesday)
    return datetime.combine(rollover_date, time(3), tzinfo=kickoff.tzinfo)


def _normalize_now(now: datetime) -> datetime:
    if not isinstance(now, datetime):
        raise TypeError("now must be a datetime.")
    if now.tzinfo is None or now.utcoffset() is None:
        return now.astimezone()
    return now


def game_week(now: datetime, kickoff_times: Mapping) -> int:
    """Return the week still in progress, advancing at Tuesday 03:00 local."""
    current_time = _normalize_now(now)
    kickoffs = _configured_kickoffs(kickoff_times)
    for week, kickoff in kickoffs:
        if current_time < _week_rollover(kickoff):
            return week
    return kickoffs[-1][0] + 1


def next_lock(now: datetime, kickoff_times: Mapping) -> tuple[int, datetime]:
    """Return the next future kickoff as a (week, local kickoff time) pair."""
    current_time = _normalize_now(now)
    for week, kickoff in _configured_kickoffs(kickoff_times):
        if current_time < kickoff:
            return week, kickoff
    raise ValueError("No future kickoff is configured.")
