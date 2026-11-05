"""Rolling 2+1 snapshot creation.

Snapshot S(n+1) is built from the FINAL committed revision of S(n) - never from
unsaved or pending work (the caller passes the committed head revision only).

    month 1 of S(n+1) = month 2 of S(n)   -> ROLLOVER (week slot by week slot)
    month 2 of S(n+1) = month 3 of S(n)   -> ROLLOVER
    month 3 of S(n+1) = month 3 of S(n)   -> CARRY_FORWARD (slot W1..W4 -> W1..W4)
    opening cash of the first interval    = calculated closing cash of S(n)
                                            at the interval just before it
    AR/AP month balances follow the same month mapping.

Blank stays blank and missing stays missing: no fictitious values are created.
When week boundaries differ between source and target, slots are mapped by
WEEK_INDEX and the mapping is reported so the user can review it.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from domain.calc import ForecastResult, ordered_weeks
from domain.metrics import METRICS, InputMode
from domain.models import (
    IcDetail,
    InputCell,
    Issue,
    Line,
    Provenance,
    RowType,
    Severity,
    ValueOrigin,
    ValueState,
    WeekPeriod,
    month_slot,
)


@dataclass
class RolloverResult:
    cells: dict[tuple[str, str], InputCell]
    ic_details: list[IcDetail]
    issues: list[Issue]


def _ic_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def build_rollover(
    *,
    business_unit: str,
    prev_snapshot: str,
    new_snapshot: str,
    source_revision_id: str,
    lines: list[Line],
    prev_calendar: list[WeekPeriod],
    new_calendar: list[WeekPeriod],
    prev_cells: dict[tuple[str, str], InputCell],
    prev_result: ForecastResult,
    prev_ic: list[IcDetail],
    actor: str,
    now_utc: str,
) -> RolloverResult:
    prev_weeks = ordered_weeks(prev_calendar)
    new_weeks = ordered_weeks(new_calendar)
    prev_periods = sorted({w.code_period_value for w in prev_weeks})
    new_periods = sorted({w.code_period_value for w in new_weeks})
    issues: list[Issue] = []

    if len(new_periods) != 3 or len(prev_periods) != 3:
        raise ValueError("Both snapshots must cover exactly three months")
    if new_periods[:2] != prev_periods[1:]:
        raise ValueError(f"Snapshot {new_snapshot} does not follow {prev_snapshot}: {prev_periods} -> {new_periods}")

    # target period -> (source period, origin)
    period_map = {
        new_periods[0]: (prev_periods[1], ValueOrigin.ROLLOVER),
        new_periods[1]: (prev_periods[2], ValueOrigin.ROLLOVER),
        new_periods[2]: (prev_periods[2], ValueOrigin.CARRY_FORWARD),
    }

    def slots(calendar: list[WeekPeriod], period: str) -> dict[int, WeekPeriod]:
        return {w.week_index: w for w in calendar if w.code_period_value == period}

    week_map: dict[str, tuple[str, str, ValueOrigin]] = {}  # new week -> (src period, src week, origin)
    for tp, (sp, origin) in period_map.items():
        t_slots, s_slots = slots(new_weeks, tp), slots(prev_weeks, sp)
        if set(t_slots) != set(s_slots):
            raise ValueError(f"Week slots differ between {sp} and {tp}: {sorted(s_slots)} vs {sorted(t_slots)}")
        for idx, tw in t_slots.items():
            sw = s_slots[idx]
            week_map[tw.code_week_period] = (sp, sw.code_week_period, origin)
            same_bounds = (tw.start, tw.end) == (sw.start, sw.end)
            if origin == ValueOrigin.ROLLOVER and not same_bounds:
                issues.append(Issue(Severity.WARNING, "W_WEEK_BOUNDARY_CHANGED",
                                    f"{tw.code_week_period} boundaries {tw.start}–{tw.end} differ from source "
                                    f"{sw.start}–{sw.end}; values mapped by slot W{idx}, please review",
                                    business_unit, tp, tw.code_week_period))
        if origin == ValueOrigin.CARRY_FORWARD:
            issues.append(Issue(Severity.INFO, "I_CARRY_FORWARD",
                                f"{tp} pre-filled from {sp} slot by slot (estimate, requires review)",
                                business_unit, tp))

    def prov(sp: str, sw: str, line_id: str) -> Provenance:
        return Provenance(prev_snapshot, sp, sw, line_id, source_revision_id, actor, now_utc)

    cells: dict[tuple[str, str], InputCell] = {}
    for ln in lines:
        mode = METRICS[ln.metric_code].input_mode
        if ln.row_type == RowType.DETAIL and mode == InputMode.LINE_INPUT:
            for tw, (sp, sw, origin) in week_map.items():
                src = prev_cells.get((ln.line_id, sw))
                state = src.state if src else ValueState.BLANK
                amount = src.amount if src and src.state == ValueState.ENTERED else None
                cells[(ln.line_id, tw)] = InputCell(ln.line_id, ln.metric_code, tw[:6], tw, amount, state, origin,
                                                    prov(sp, sw, ln.line_id))
        elif mode == InputMode.MONTH_STATE_INPUT:
            for tp, (sp, origin) in period_map.items():
                src = prev_cells.get((ln.line_id, month_slot(sp)))
                entered = src is not None and src.state == ValueState.ENTERED
                cells[(ln.line_id, month_slot(tp))] = InputCell(
                    ln.line_id, ln.metric_code, tp, month_slot(tp),
                    src.amount if entered else None,
                    ValueState.ENTERED if entered else ValueState.MISSING,
                    origin, prov(sp, month_slot(sp), ln.line_id))
        elif mode == InputMode.OPENING_FIRST:
            first_new = new_weeks[0]
            # interval immediately before the new first interval in the old snapshot
            idx = next(i for i, w in enumerate(prev_weeks) if w.code_period_value == first_new.code_period_value)
            before = prev_weeks[idx - 1]
            closing = prev_result.metric_weekly[("CLOSING_CASH_BALANCE", before.code_week_period)].amount
            cells[(ln.line_id, first_new.code_week_period)] = InputCell(
                ln.line_id, ln.metric_code, first_new.code_period_value, first_new.code_week_period,
                closing, ValueState.ENTERED if closing is not None else ValueState.MISSING,
                ValueOrigin.ROLLOVER, prov(before.code_period_value, before.code_week_period, ln.line_id))
            if closing is None:
                issues.append(Issue(Severity.BLOCKING, "B_OPENING_MISSING",
                                    f"Closing cash of {before.code_week_period} in {prev_snapshot} is unknown; "
                                    f"opening balance must be entered", business_unit,
                                    first_new.code_period_value, first_new.code_week_period, ln.line_id,
                                    "OPENING_CASH_BALANCE"))

    ic: list[IcDetail] = []
    for tw, (sp, sw, origin) in week_map.items():
        for d in prev_ic:
            if d.code_week_period == sw:
                ic.append(replace(d, detail_id=_ic_id(new_snapshot, business_unit, tw, d.detail_id),
                                  code_period_value=tw[:6], code_week_period=tw, origin=origin))

    return RolloverResult(cells, ic, issues)
