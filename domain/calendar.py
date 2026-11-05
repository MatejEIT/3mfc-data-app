"""Business-week calendar helpers.

Observed rule in the source Excel (Oct-Dec 2026, verified against
DIM_3MFC_WEEK_PERIOD): W4 starts on the last Monday of the month that leaves at
least 2 days in the month; W3 and W2 are the two preceding Monday-Sunday weeks;
W1 is everything before W2. Generated calendars are PROPOSALS only and must be
confirmed by HQ before use.
"""
from __future__ import annotations

import calendar as _cal
from datetime import date, timedelta

from domain.models import CalendarStatus, WeekPeriod


def next_period(period: str, months: int = 1) -> str:
    y, m = int(period[:4]), int(period[4:])
    idx = y * 12 + (m - 1) + months
    return f"{idx // 12:04d}{idx % 12 + 1:02d}"


def snapshot_periods(snapshot: str) -> list[str]:
    return [snapshot, next_period(snapshot, 1), next_period(snapshot, 2)]


def propose_month(period: str) -> list[WeekPeriod]:
    y, m = int(period[:4]), int(period[4:])
    last_day = date(y, m, _cal.monthrange(y, m)[1])
    w4 = last_day - timedelta(days=last_day.weekday())   # last Monday
    if (last_day - w4).days < 1:                          # W4 would have a single day
        w4 -= timedelta(days=7)
    w3, w2 = w4 - timedelta(days=7), w4 - timedelta(days=14)
    bounds = [(date(y, m, 1), w2 - timedelta(days=1)), (w2, w3 - timedelta(days=1)),
              (w3, w4 - timedelta(days=1)), (w4, last_day)]
    return [WeekPeriod(period, i + 1, f"{period}_W{i + 1}", s, e, CalendarStatus.PROPOSED_CONFIRM_REQUIRED)
            for i, (s, e) in enumerate(bounds)]


def validate_month(weeks: list[WeekPeriod]) -> list[str]:
    """Four contiguous, non-overlapping intervals exactly covering the month."""
    problems: list[str] = []
    ws = sorted(weeks, key=lambda w: w.week_index)
    if [w.week_index for w in ws] != [1, 2, 3, 4]:
        return [f"Expected W1..W4, got {[w.week_index for w in ws]}"]
    period = ws[0].code_period_value
    y, m = int(period[:4]), int(period[4:])
    if ws[0].start != date(y, m, 1):
        problems.append("W1 must start on the 1st")
    if ws[-1].end != date(y, m, _cal.monthrange(y, m)[1]):
        problems.append("W4 must end on the last day of the month")
    for a, b in zip(ws, ws[1:]):
        if b.start != a.end + timedelta(days=1):
            problems.append(f"Gap/overlap between W{a.week_index} and W{b.week_index}")
    for w in ws:
        if w.end < w.start:
            problems.append(f"W{w.week_index} ends before it starts")
    return problems
