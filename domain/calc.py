"""Calculation engine: weekly inputs -> weekly metrics -> monthly metrics.

Weekly business intervals are the ONLY editable granularity for flows; monthly
values are always derived here, so Weekly and Monthly can never diverge.

Formulas
    SUBTOTAL / TOTAL         = sum of child metrics (DETAIL lines summed per metric)
    NET_CASH_MOVEMENT        = TOTAL_CASH_INFLOW - TOTAL_CASH_OUTFLOW + BANK_FINANCING_MOVEMENT
    OPENING_CASH_BALANCE(w)  = input for the first interval of the horizon,
                               otherwise CLOSING_CASH_BALANCE(w-1)
    CLOSING_CASH_BALANCE(w)  = OPENING_CASH_BALANCE(w) + NET_CASH_MOVEMENT(w)
Monthly
    FLOW         SUM(W1..W4)
    OPENING      OPENING(first interval of month)
    CLOSING      CLOSING(last interval of month)
    MONTH_STATE  month-end input value (AR/AP balances)

A missing opening balance is NOT replaced by zero: opening/closing stay None
(unknown) for the whole chain and a BLOCKING validation is raised.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from domain.metrics import (
    METRICS,
    ROLLUP_ORDER,
    Aggregation,
    InputMode,
    children_of,
)
from domain.models import InputCell, Line, RowType, ValueState, WeekPeriod, month_slot

ZERO = Decimal(0)


@dataclass(frozen=True)
class CalcValue:
    amount: Decimal | None      # None = unknown (e.g. missing opening balance)
    has_input: bool             # True if at least one underlying cell was ENTERED

    @staticmethod
    def unknown() -> "CalcValue":
        return CalcValue(None, False)


def _add(a: CalcValue, b: CalcValue) -> CalcValue:
    if a.amount is None or b.amount is None:
        return CalcValue(None, a.has_input or b.has_input)
    return CalcValue(a.amount + b.amount, a.has_input or b.has_input)


def _neg(a: CalcValue) -> CalcValue:
    return a if a.amount is None else CalcValue(-a.amount, a.has_input)


def _cell_value(cell: InputCell | None) -> CalcValue:
    if cell is None or cell.state in (ValueState.BLANK, ValueState.MISSING) or cell.amount is None:
        return CalcValue(ZERO, False)
    return CalcValue(cell.amount, True)


@dataclass
class ForecastResult:
    weeks: list[WeekPeriod]
    periods: list[str]
    metric_weekly: dict[tuple[str, str], CalcValue]   # (metric, week_code)
    metric_monthly: dict[tuple[str, str], CalcValue]  # (metric, period)
    line_weekly: dict[tuple[str, str], CalcValue]     # (line_id, week_code)
    line_monthly: dict[tuple[str, str], CalcValue]    # (line_id, period)

    def kpi(self, metric: str, period: str) -> Decimal | None:
        return self.metric_monthly.get((metric, period), CalcValue.unknown()).amount


def ordered_weeks(calendar: Iterable[WeekPeriod]) -> list[WeekPeriod]:
    return sorted(calendar, key=lambda w: (w.code_period_value, w.week_index))


def first_week_code(calendar: Iterable[WeekPeriod]) -> str:
    return ordered_weeks(calendar)[0].code_week_period


def is_cell_editable(line: Line, week_code: str, first_week: str) -> bool:
    """Single source of truth for editability (enforced again server-side)."""
    mode = METRICS[line.metric_code].input_mode
    if mode == InputMode.LINE_INPUT:
        return not week_code.endswith("_M")
    if mode == InputMode.OPENING_FIRST:
        return week_code == first_week
    if mode == InputMode.MONTH_STATE_INPUT:
        return week_code.endswith("_M")
    return False


def calculate(lines: list[Line], calendar: list[WeekPeriod], cells: dict[tuple[str, str], InputCell]) -> ForecastResult:
    weeks = ordered_weeks(calendar)
    if not weeks:
        raise ValueError("Calendar is empty")
    periods = sorted({w.code_period_value for w in weeks})

    detail_lines = [ln for ln in lines if ln.row_type == RowType.DETAIL]
    opening_line = next((ln for ln in lines if ln.metric_code == "OPENING_CASH_BALANCE"), None)

    metric_weekly: dict[tuple[str, str], CalcValue] = {}
    prev_closing: CalcValue | None = None

    for i, w in enumerate(weeks):
        wk = w.code_week_period
        acc: dict[str, CalcValue] = defaultdict(lambda: CalcValue(ZERO, False))
        for ln in detail_lines:
            acc[ln.metric_code] = _add(acc[ln.metric_code], _cell_value(cells.get((ln.line_id, wk))))
        for code in ROLLUP_ORDER:
            total = CalcValue(ZERO, False)
            for child in children_of(code):
                total = _add(total, acc[child])
            acc[code] = total
        net = _add(_add(acc["TOTAL_CASH_INFLOW"], _neg(acc["TOTAL_CASH_OUTFLOW"])), acc["BANK_FINANCING_MOVEMENT"])
        acc["NET_CASH_MOVEMENT"] = net

        if i == 0:
            cell = cells.get((opening_line.line_id, wk)) if opening_line else None
            if cell is None or cell.state != ValueState.ENTERED or cell.amount is None:
                opening = CalcValue.unknown()
            else:
                opening = CalcValue(cell.amount, True)
        else:
            opening = prev_closing  # type: ignore[assignment]
        closing = _add(opening, net) if opening.amount is not None else CalcValue(None, net.has_input)
        acc["OPENING_CASH_BALANCE"] = opening
        acc["CLOSING_CASH_BALANCE"] = closing
        prev_closing = closing

        for code, val in acc.items():
            metric_weekly[(code, wk)] = val

    # ---- monthly aggregation ------------------------------------------------
    metric_monthly: dict[tuple[str, str], CalcValue] = {}
    weeks_by_period: dict[str, list[WeekPeriod]] = defaultdict(list)
    for w in weeks:
        weeks_by_period[w.code_period_value].append(w)

    for code, m in METRICS.items():
        for p in periods:
            pw = weeks_by_period[p]
            if m.aggregation == Aggregation.FLOW:
                total = CalcValue(ZERO, False)
                for w in pw:
                    total = _add(total, metric_weekly.get((code, w.code_week_period), CalcValue(ZERO, False)))
                metric_monthly[(code, p)] = total
            elif m.aggregation == Aggregation.OPENING:
                metric_monthly[(code, p)] = metric_weekly[(code, pw[0].code_week_period)]
            elif m.aggregation == Aggregation.CLOSING:
                metric_monthly[(code, p)] = metric_weekly[(code, pw[-1].code_week_period)]
            else:  # MONTH_STATE
                vals = [cells.get((ln.line_id, month_slot(p))) for ln in lines if ln.metric_code == code]
                entered = [c for c in vals if c is not None and c.state == ValueState.ENTERED and c.amount is not None]
                metric_monthly[(code, p)] = (
                    CalcValue(sum((c.amount for c in entered), ZERO), True) if entered else CalcValue.unknown()
                )

    # ---- line level ----------------------------------------------------------
    line_weekly: dict[tuple[str, str], CalcValue] = {}
    line_monthly: dict[tuple[str, str], CalcValue] = {}
    for ln in lines:
        m = METRICS[ln.metric_code]
        for w in weeks:
            wk = w.code_week_period
            if ln.row_type == RowType.DETAIL:
                line_weekly[(ln.line_id, wk)] = _cell_value(cells.get((ln.line_id, wk)))
            elif m.aggregation != Aggregation.MONTH_STATE:
                line_weekly[(ln.line_id, wk)] = metric_weekly[(ln.metric_code, wk)]
        for p in periods:
            if ln.row_type == RowType.DETAIL:
                total = CalcValue(ZERO, False)
                for w in weeks_by_period[p]:
                    total = _add(total, line_weekly[(ln.line_id, w.code_week_period)])
                line_monthly[(ln.line_id, p)] = total
            elif m.aggregation == Aggregation.MONTH_STATE:
                c = cells.get((ln.line_id, month_slot(p)))
                line_monthly[(ln.line_id, p)] = _cell_value(c) if c and c.state == ValueState.ENTERED else CalcValue.unknown()
            else:
                line_monthly[(ln.line_id, p)] = metric_monthly[(ln.metric_code, p)]

    return ForecastResult(weeks, periods, metric_weekly, metric_monthly, line_weekly, line_monthly)


def to_eur(amount: Decimal | None, fx_rate: Decimal | None) -> Decimal | None:
    """FX_RATE = units of local currency per 1 EUR. Missing FX -> None, never a fake rate."""
    if amount is None or fx_rate is None or fx_rate == 0:
        return None
    return amount / fx_rate
