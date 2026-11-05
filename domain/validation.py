"""Business and technical validation. Runs server-side on every save and submit.

Severity policy
    BLOCKING  save (edit-level rules) or submit is refused
    WARNING   shown, does not block
    INFO      informational
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from domain.calc import ForecastResult, is_cell_editable, ordered_weeks
from domain.metrics import AR_AP_METRICS, IC_SPLIT_METRIC, METRICS
from domain.models import (
    IcDetail,
    InputCell,
    Issue,
    Line,
    Severity,
    ValueOrigin,
    ValueState,
    WeekPeriod,
    month_slot,
)

MAX_ABS_AMOUNT = Decimal("1e13")       # 10 trillion LCY: anything bigger is a typo
MANUAL_MAX_DECIMALS = 2
IC_TOLERANCE = Decimal("0.01")

_AMBIGUOUS_THOUSANDS = re.compile(r"^-?\d{1,3}[.,]\d{3}$")


class AmountParseError(ValueError):
    pass


def parse_amount(raw: object) -> Decimal | None:
    """Parse user input. Returns None for an empty cell (never 0).

    Accepts 1234.5, 1 234,50, 1.234,50, 1,234.50, (1234) for negatives, -1234.
    Rejects NaN/Infinity, text, and ambiguous values such as "1,234" or "1.234"
    (thousands separator or decimal?) - the user must disambiguate.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise AmountParseError("Boolean is not an amount")
    if isinstance(raw, (int, Decimal)):
        value = Decimal(raw)
    elif isinstance(raw, float):
        if math.isnan(raw) or math.isinf(raw):
            # pandas represents an empty numeric cell as NaN
            if math.isnan(raw):
                return None
            raise AmountParseError("Infinity is not allowed")
        value = Decimal(repr(raw))
    else:
        s = str(raw).strip().replace(" ", "").replace(" ", "").replace("'", "")
        if s == "":
            return None
        if s.lower() in {"nan", "inf", "-inf", "infinity", "-infinity", "null", "none"}:
            raise AmountParseError(f"'{raw}' is not a valid amount")
        negative = s.startswith("(") and s.endswith(")")
        if negative:
            s = s[1:-1]
        if _AMBIGUOUS_THOUSANDS.match(s):
            raise AmountParseError(f"'{raw}' is ambiguous (thousands separator or decimal?)")
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            if s.count(",") > 1:
                s = s.replace(",", "")
            else:
                s = s.replace(",", ".")
        elif s.count(".") > 1:
            s = s.replace(".", "")
        try:
            value = Decimal(s)
        except InvalidOperation as exc:
            raise AmountParseError(f"'{raw}' is not a number") from exc
        if negative:
            value = -value
    if not value.is_finite():
        raise AmountParseError("Non-finite numbers are not allowed")
    if abs(value) > MAX_ABS_AMOUNT:
        raise AmountParseError(f"{value} exceeds the allowed magnitude")
    return value


@dataclass(frozen=True)
class Edit:
    line_id: str
    week_code: str
    raw_value: object


def validate_edits(
    edits: list[Edit],
    lines_by_id: dict[str, Line],
    calendar: list[WeekPeriod],
    business_unit: str,
) -> tuple[list[tuple[Edit, Decimal | None]], list[Issue]]:
    """Edit-level checks: parsable, finite, precision, cell exists and is editable."""
    valid_weeks = {w.code_week_period for w in calendar} | {month_slot(w.code_period_value) for w in calendar}
    first = ordered_weeks(calendar)[0].code_week_period
    ok: list[tuple[Edit, Decimal | None]] = []
    issues: list[Issue] = []
    seen: set[tuple[str, str]] = set()
    for e in edits:
        ctx = dict(business_unit=business_unit, week=e.week_code, line_id=e.line_id)
        line = lines_by_id.get(e.line_id)
        if line is None:
            issues.append(Issue(Severity.BLOCKING, "E_UNKNOWN_LINE", "Unknown forecast line", **ctx))
            continue
        if e.week_code not in valid_weeks:
            issues.append(Issue(Severity.BLOCKING, "E_UNKNOWN_PERIOD", "Period is not part of this snapshot", **ctx))
            continue
        if (e.line_id, e.week_code) in seen:
            issues.append(Issue(Severity.BLOCKING, "E_DUPLICATE_KEY", "Same cell edited twice in one save", **ctx))
            continue
        seen.add((e.line_id, e.week_code))
        if not is_cell_editable(line, e.week_code, first):
            issues.append(Issue(Severity.BLOCKING, "E_NOT_EDITABLE",
                                f"{METRICS[line.metric_code].label} is calculated and cannot be edited",
                                metric_code=line.metric_code, **ctx))
            continue
        try:
            value = parse_amount(e.raw_value)
        except AmountParseError as exc:
            issues.append(Issue(Severity.BLOCKING, "E_NOT_NUMERIC", str(exc), metric_code=line.metric_code, **ctx))
            continue
        if value is not None and value != 0 and -value.normalize().as_tuple().exponent > MANUAL_MAX_DECIMALS:  # type: ignore[operator]
            issues.append(Issue(Severity.BLOCKING, "E_PRECISION",
                                f"Max {MANUAL_MAX_DECIMALS} decimal places allowed", metric_code=line.metric_code, **ctx))
            continue
        ok.append((e, value))
    return ok, issues


def validate_forecast(
    business_unit: str,
    lines: list[Line],
    calendar: list[WeekPeriod],
    cells: dict[tuple[str, str], InputCell],
    result: ForecastResult,
    ic_details: list[IcDetail],
    valid_counterparties: set[str],
    fx_available: dict[str, bool],
    carry_forward_reviewed: set[str],
) -> list[Issue]:
    """Full business validation (run before submit and shown in Validations)."""
    issues: list[Issue] = []
    bu = business_unit
    weeks = ordered_weeks(calendar)
    first = weeks[0]

    # Opening cash balance of the first interval is mandatory.
    opening = next((ln for ln in lines if ln.metric_code == "OPENING_CASH_BALANCE"), None)
    cell = cells.get((opening.line_id, first.code_week_period)) if opening else None
    if cell is None or cell.state != ValueState.ENTERED:
        issues.append(Issue(Severity.BLOCKING, "B_OPENING_MISSING",
                            "Opening cash balance of the first interval is missing",
                            bu, first.code_period_value, first.code_week_period,
                            opening.line_id if opening else "", "OPENING_CASH_BALANCE"))

    # AR/AP month-end balances are required for every forecast month.
    for metric in AR_AP_METRICS:
        mlines = [ln for ln in lines if ln.metric_code == metric]
        for p in result.periods:
            c = cells.get((mlines[0].line_id, month_slot(p))) if mlines else None
            if c is None or c.state != ValueState.ENTERED:
                issues.append(Issue(Severity.BLOCKING, "B_AR_AP_MISSING",
                                    f"{METRICS[metric].label} for {p} is missing (empty is not zero)",
                                    bu, p, month_slot(p), mlines[0].line_id if mlines else "", metric))

    # Negative cash.
    for w in weeks:
        v = result.metric_weekly[("CLOSING_CASH_BALANCE", w.code_week_period)].amount
        if v is not None and v < 0:
            issues.append(Issue(Severity.WARNING, "W_NEGATIVE_CASH", f"Negative closing cash balance {v:,.0f}",
                                bu, w.code_period_value, w.code_week_period, metric_code="CLOSING_CASH_BALANCE"))

    # IC split must reconcile with row 89 per interval; counterparties must be valid.
    ic_line = next((ln for ln in lines if ln.metric_code == IC_SPLIT_METRIC), None)
    for d in ic_details:
        if d.counterparty not in valid_counterparties:
            issues.append(Issue(Severity.BLOCKING, "B_IC_COUNTERPARTY", f"Unknown counterparty '{d.counterparty}'",
                                bu, d.code_period_value, d.code_week_period, metric_code=IC_SPLIT_METRIC))
        elif d.counterparty == bu:
            issues.append(Issue(Severity.BLOCKING, "B_IC_SELF", "Entity cannot be its own IC counterparty",
                                bu, d.code_period_value, d.code_week_period, metric_code=IC_SPLIT_METRIC))
    if ic_line:
        for w in weeks:
            main = result.line_weekly[(ic_line.line_id, w.code_week_period)].amount or Decimal(0)
            split = sum((d.amount for d in ic_details if d.code_week_period == w.code_week_period), Decimal(0))
            if abs(main - split) > IC_TOLERANCE:
                issues.append(Issue(Severity.BLOCKING, "B_IC_UNALLOCATED",
                                    f"IC other outflow {main:,.2f} vs counterparty split {split:,.2f} "
                                    f"(unallocated {main - split:,.2f})",
                                    bu, w.code_period_value, w.code_week_period, ic_line.line_id, IC_SPLIT_METRIC))

    # Carry-forward month must be reviewed explicitly by the entity.
    cf_periods = sorted({c.code_period_value for c in cells.values() if c.origin == ValueOrigin.CARRY_FORWARD})
    for p in cf_periods:
        if p not in carry_forward_reviewed:
            issues.append(Issue(Severity.BLOCKING, "B_CARRY_FORWARD_UNREVIEWED",
                                f"{p} was pre-filled from the previous month (CARRY_FORWARD) and must be reviewed",
                                bu, p))

    # Calendar still a proposal.
    for p in sorted({w.code_period_value for w in weeks if w.needs_confirmation}):
        issues.append(Issue(Severity.WARNING, "W_CALENDAR_PROPOSED",
                            f"Business-week calendar for {p} is a proposal and must be confirmed by HQ", bu, p))

    # FX availability (HQ responsibility; entity submit is not blocked).
    for p in result.periods:
        if not fx_available.get(p, False):
            issues.append(Issue(Severity.WARNING, "W_FX_MISSING",
                                f"No FX rate for {p}; EUR values cannot be calculated", bu, p))

    # Detail lines without any input in the whole horizon (information only).
    if not any(c.state == ValueState.ENTERED for c in cells.values()):
        issues.append(Issue(Severity.WARNING, "W_EMPTY_FORECAST", "No values entered", bu))

    return issues


def has_blocking(issues: list[Issue]) -> bool:
    return any(i.severity == Severity.BLOCKING for i in issues)
