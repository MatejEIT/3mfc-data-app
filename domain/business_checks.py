"""Pre-submit checks shown in the 'Validate & submit' pop-up.

Technical checks (BLOCKING) – the forecast cannot be submitted:
    B_MONTH_EMPTY            a forecast month has no value at all
    (plus number format / opening cash / carry-forward review, see domain/validation.py)

Business checks (WARNING) – plausibility only; the entity may submit anyway with a comment:
    W_NEGATIVE_INFLOW        a cash inflow line is negative
    W_NEGATIVE_OUTFLOW       a cash outflow line is negative (outflows are entered as positive numbers)
    W_PREV_SNAPSHOT_DEVIATION  month-end cash differs > ±30 % from the previous snapshot's forecast
    W_ACTUALS_DEVIATION      average month-end cash differs > ±30 % from the last 6 closed months (IDL actuals)

Questions (INFO, code prefix Q_) – fixed reminders for specific companies, e.g. V004 factoring.
"""
from __future__ import annotations

from decimal import Decimal

from domain.calc import ForecastResult
from domain.metrics import METRICS, InputMode, Section
from domain.models import InputCell, Issue, Line, Severity, ValueState

DEVIATION_LIMIT = Decimal("0.30")
ACTUAL_MONTHS = 6

# Fixed questions per company (matched on the 4-character company code, e.g. V004 -> V004AE).
ENTITY_QUESTIONS: dict[str, list[str]] = {
    "V004": ["Have you thought about the factoring repayment?"],
}


def deviation(value: Decimal | None, base: Decimal | None) -> Decimal | None:
    """Relative difference (value - base) / |base|; None when it cannot be computed (missing or zero base)."""
    if value is None or base is None or base == 0:
        return None
    return (value - base) / abs(base)


def _fmt_k(v: Decimal) -> str:
    return f"{v / 1000:,.0f}k"


def _pct(d: Decimal) -> str:
    return f"{d * 100:+.0f} %"


def _flow_lines(lines: list[Line]) -> list[Line]:
    return [ln for ln in lines if METRICS[ln.metric_code].input_mode == InputMode.LINE_INPUT
            and METRICS[ln.metric_code].section in (Section.INFLOW, Section.OUTFLOW, Section.FINANCING)]


def check_months_filled(bu: str, lines: list[Line], cells: dict[tuple[str, str], InputCell],
                        periods: list[str]) -> list[Issue]:
    flow = {ln.line_id for ln in _flow_lines(lines)}
    filled = {c.code_period_value for (lid, _), c in cells.items()
              if lid in flow and c.state == ValueState.ENTERED}
    return [Issue(Severity.BLOCKING, "B_MONTH_EMPTY",
                  f"No cash-flow values entered for {p} – enter at least the expected inflows and outflows "
                  "(0 is a valid value)", bu, p)
            for p in periods if p not in filled]


def check_signs(bu: str, lines: list[Line], cells: dict[tuple[str, str], InputCell],
                label_of=lambda ln: ln.label) -> list[Issue]:
    """One warning per line with negative values (not one per cell)."""
    issues = []
    for ln in _flow_lines(lines):
        section = METRICS[ln.metric_code].section
        if section == Section.FINANCING:            # bank financing is +/- by definition
            continue
        neg = sorted(w for (lid, w), c in cells.items()
                     if lid == ln.line_id and c.state == ValueState.ENTERED and c.amount is not None and c.amount < 0)
        if not neg:
            continue
        inflow = section == Section.INFLOW
        what = "Cash inflow" if inflow else "Cash outflow"
        hint = ("inflows should not be negative" if inflow
                else "outflows are entered as positive numbers")
        issues.append(Issue(Severity.WARNING, "W_NEGATIVE_INFLOW" if inflow else "W_NEGATIVE_OUTFLOW",
                            f"{what} '{label_of(ln)}' is negative in {len(neg)} week(s) – {hint}",
                            bu, neg[0][:6], neg[0], ln.line_id, ln.metric_code))
    return issues


def check_vs_previous(bu: str, result: ForecastResult, prev: ForecastResult | None,
                      prev_snapshot: str) -> list[Issue]:
    if prev is None:
        return []
    issues = []
    for p in result.periods:
        if p not in prev.periods:
            continue
        now, before = result.kpi("CLOSING_CASH_BALANCE", p), prev.kpi("CLOSING_CASH_BALANCE", p)
        d = deviation(now, before)
        if d is not None and abs(d) > DEVIATION_LIMIT:
            issues.append(Issue(Severity.WARNING, "W_PREV_SNAPSHOT_DEVIATION",
                                f"Closing cash {p}: {_fmt_k(now)} vs {_fmt_k(before)} in snapshot {prev_snapshot} "
                                f"({_pct(d)}, limit ±{DEVIATION_LIMIT * 100:.0f} %)",
                                bu, p, metric_code="CLOSING_CASH_BALANCE"))
    return issues


def check_vs_actuals(bu: str, result: ForecastResult, actual_closing: dict[str, Decimal] | None) -> list[Issue]:
    """Average forecast month-end cash vs the average of the last ACTUAL_MONTHS closed months."""
    if not actual_closing:
        return [Issue(Severity.INFO, "I_NO_ACTUALS", "No cash actuals available for comparison", bu)]
    months = sorted(actual_closing)[-ACTUAL_MONTHS:]
    base = sum((actual_closing[m] for m in months), Decimal(0)) / len(months)
    fc = [v for p in result.periods if (v := result.kpi("CLOSING_CASH_BALANCE", p)) is not None]
    if not fc:
        return []
    avg = sum(fc, Decimal(0)) / len(fc)
    d = deviation(avg, base)
    if d is None or abs(d) <= DEVIATION_LIMIT:
        return []
    return [Issue(Severity.WARNING, "W_ACTUALS_DEVIATION",
                  f"Average forecast closing cash {_fmt_k(avg)} vs {_fmt_k(base)} average actual cash "
                  f"{months[0]}–{months[-1]} ({_pct(d)}, limit ±{DEVIATION_LIMIT * 100:.0f} %)",
                  bu, metric_code="CLOSING_CASH_BALANCE")]


def entity_questions(bu: str) -> list[Issue]:
    return [Issue(Severity.INFO, "Q_ENTITY", q, bu) for q in ENTITY_QUESTIONS.get(bu[:4], [])]
