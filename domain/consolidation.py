"""Group consolidation in kEUR – replica of the client's Excel sheet "FC CONSO" (decision 2026-11-05: option B).

Rules (docs/CONSOLIDATION_SPEC.md, A.3/A.4):
* kEUR = LCY / rate / 1000, rate = LCY per 1 EUR (IDL SK (BS) of the month before the snapshot), one rate
  for the whole horizon. A company without a rate is NOT consolidated (no fallback, no manual rate).
* IC is eliminated: IC receipts (template rows 64/65) and IC payments (88/89) are not in group inflow /
  outflow / net; they are shown as memo lines with the check IC in − IC out (= 0).
* Net Cash Movement (group) = Total inflow − Total outflow + Borrowing (external flows only).
* Opening: first week of a month = Σ company openings of that week; following weeks = previous group
  closing. Closing = opening + net. Month column: opening of W1, closing of the last week.
* Empty cells stay empty in the companies' data; in sums an optional empty line counts as 0 (as in the app).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from domain.calc import ForecastResult
from domain.models import Line, WeekPeriod

ZERO = Decimal(0)
IC_TOLERANCE_KEUR = Decimal(10)

IC_IN_ROWS = {64, 65}                      # OTHER_RECEIPTS_DETAIL rows that are intercompany
OTHER_IN_METRIC = "OTHER_RECEIPTS_DETAIL"

# (key, label, kind) - kind: section | line | sum | total | memo | check
CONSO_ROWS: list[tuple[str, str, str]] = [
    ("_in", "CASH INFLOW", "section"),
    ("ar_not_overdue", "Payments from existing invoices – not overdue", "line"),
    ("ar_overdue", "Payments from existing invoices – overdue", "line"),
    ("new_invoices", "New invoices issued (expected collection)", "line"),
    ("other_in", "Other cash inflow (interest, taxes)", "line"),
    ("total_in", "TOTAL CASH INFLOW", "total"),
    ("_out", "CASH OUTFLOW", "section"),
    ("material", "Material expenses", "line"),
    ("local_purchasing", "Local purchasing 3 months payments (> 500 kEUR)", "line"),
    ("project", "Project / service expenses", "line"),
    ("mat_out", "Cash outflow material expenses TP", "sum"),
    ("employment", "Employment", "line"),
    ("investments", "Investments", "line"),
    ("taxes", "Taxes", "line"),
    ("other_exp", "Other expenses", "line"),
    ("oth_out", "Cash outflow others", "sum"),
    ("total_out", "TOTAL CASH OUTFLOW", "total"),
    ("borrowing", "Borrowing (+) / repayment (−) of bank loan", "line"),
    ("net", "Net Cash Movement", "total"),
    ("_bal", "CASH BALANCE", "section"),
    ("opening", "Opening Cash Balance", "line"),
    ("net_bal", "Net Cash Movement", "line"),
    ("closing", "Closing Cash Balance", "total"),
    ("_ic", "INTERCOMPANY (eliminated)", "section"),
    ("ic_in", "Cash inflow IC (memo)", "memo"),
    ("ic_out", "Cash outflow IC (memo)", "memo"),
    ("ic_diff", "Check (= 0)", "check"),
]

FLOW_KEYS = ["ar_not_overdue", "ar_overdue", "new_invoices", "other_in", "material", "local_purchasing", "project",
             "employment", "investments", "taxes", "other_exp", "borrowing", "ic_in", "ic_out"]

_METRIC_OF = {
    "ar_not_overdue": ["AR_NOT_OVERDUE_COLLECTIONS"], "ar_overdue": ["AR_OVERDUE_COLLECTIONS"],
    "new_invoices": ["NEW_INVOICE_COLLECTIONS"], "material": ["MATERIAL_EXPENSES"],
    "local_purchasing": ["LOCAL_PURCHASING"], "project": ["PROJECT_SERVICE_EXPENSES", "OTHER_PROJECT_EXPENSES"],
    "employment": ["EMPLOYMENT"], "investments": ["INVESTMENTS"], "taxes": ["TAXES"],
    "other_exp": ["OTHER_EXPENSES"], "borrowing": ["BANK_FINANCING_MOVEMENT"],
    "ic_out": ["TOTAL_INTERCOMPANY_OUTFLOW"],
}


def _v(x) -> Decimal:
    return x.amount if x is not None and x.amount is not None else ZERO


@dataclass
class CompanyFlows:
    """One company, weekly values in kEUR (and the rate used)."""
    business_unit: str
    rate: Decimal
    weekly: dict[str, dict[str, Decimal]]          # key -> week -> kEUR
    opening: dict[str, Decimal | None]             # week -> company opening kEUR (as entered / chained)
    closing: dict[str, Decimal | None]             # week -> company closing kEUR (incl. IC)
    month_closing: dict[str, Decimal | None]       # period -> company closing kEUR
    has_values: bool = True


def company_flows(bu: str, lines: list[Line], weeks: list[WeekPeriod], result: ForecastResult,
                  rate: Decimal) -> CompanyFlows:
    k = Decimal(1000) * rate
    weekly: dict[str, dict[str, Decimal]] = {key: {} for key in FLOW_KEYS}
    ic_in_lines = [ln.line_id for ln in lines if ln.metric_code == OTHER_IN_METRIC and ln.source_row in IC_IN_ROWS]
    other_in_lines = [ln.line_id for ln in lines if ln.metric_code == OTHER_IN_METRIC
                      and ln.source_row not in IC_IN_ROWS]
    any_value = False
    for w in weeks:
        wc = w.code_week_period
        for key, metrics in _METRIC_OF.items():
            weekly[key][wc] = sum((_v(result.metric_weekly.get((m, wc))) for m in metrics), ZERO) / k
        weekly["ic_in"][wc] = sum((_v(result.line_weekly.get((lid, wc))) for lid in ic_in_lines), ZERO) / k
        weekly["other_in"][wc] = sum((_v(result.line_weekly.get((lid, wc))) for lid in other_in_lines), ZERO) / k
        any_value = any_value or any(weekly[key][wc] != 0 for key in FLOW_KEYS)

    def conv(x):
        return None if x is None or x.amount is None else x.amount / k
    opening = {w.code_week_period: conv(result.metric_weekly.get(("OPENING_CASH_BALANCE", w.code_week_period)))
               for w in weeks}
    closing = {w.code_week_period: conv(result.metric_weekly.get(("CLOSING_CASH_BALANCE", w.code_week_period)))
               for w in weeks}
    months = sorted({w.code_period_value for w in weeks})
    month_closing = {p: conv(result.metric_monthly.get(("CLOSING_CASH_BALANCE", p))) for p in months}
    any_value = any_value or any(v not in (None, ZERO) for v in opening.values())
    return CompanyFlows(bu, rate, weekly, opening, closing, month_closing, any_value)


@dataclass
class Consolidation:
    weeks: list[WeekPeriod]
    periods: list[str]
    weekly: dict[str, dict[str, Decimal]] = field(default_factory=dict)    # row key -> week -> kEUR
    monthly: dict[str, dict[str, Decimal]] = field(default_factory=dict)   # row key -> period / "TOTAL" -> kEUR
    companies: list[CompanyFlows] = field(default_factory=list)

    def value(self, key: str, col: str) -> Decimal | None:
        if col in self.monthly.get(key, {}):
            return self.monthly[key][col]
        return self.weekly.get(key, {}).get(col)


def consolidate(companies: list[CompanyFlows], weeks: list[WeekPeriod]) -> Consolidation:
    weeks = sorted(weeks, key=lambda w: (w.code_period_value, w.week_index))
    periods = sorted({w.code_period_value for w in weeks})
    c = Consolidation(weeks, periods, companies=companies)
    wk = [w.code_week_period for w in weeks]
    for key in FLOW_KEYS:
        c.weekly[key] = {wc: sum((x.weekly[key][wc] for x in companies), ZERO) for wc in wk}
    g = c.weekly
    for wc in wk:
        g.setdefault("total_in", {})[wc] = sum(g[k][wc] for k in ("ar_not_overdue", "ar_overdue", "new_invoices",
                                                                   "other_in"))
        g.setdefault("mat_out", {})[wc] = g["material"][wc] + g["local_purchasing"][wc] + g["project"][wc]
        g.setdefault("oth_out", {})[wc] = sum(g[k][wc] for k in ("employment", "investments", "taxes", "other_exp"))
        g.setdefault("total_out", {})[wc] = g["mat_out"][wc] + g["oth_out"][wc]
        g.setdefault("net", {})[wc] = g["total_in"][wc] - g["total_out"][wc] + g["borrowing"][wc]
        g.setdefault("net_bal", {})[wc] = g["net"][wc]
        g.setdefault("ic_diff", {})[wc] = g["ic_in"][wc] - g["ic_out"][wc]
    g["opening"], g["closing"] = {}, {}
    prev = None
    for w in weeks:
        wc = w.code_week_period
        if prev is not None and prev.code_period_value == w.code_period_value:
            g["opening"][wc] = g["closing"][prev.code_week_period]
        else:                                    # first week of a month: re-based on the companies' openings
            g["opening"][wc] = sum((x.opening[wc] or ZERO for x in companies), ZERO)
        g["closing"][wc] = g["opening"][wc] + g["net"][wc]
        prev = w

    def month_cols(key: str, wcs: list[str]) -> Decimal:
        if key == "opening":
            return g[key][wcs[0]]
        if key == "closing":
            return g[key][wcs[-1]]
        return sum((g[key][x] for x in wcs), ZERO)

    for key in g:
        c.monthly[key] = {p: month_cols(key, [w.code_week_period for w in weeks if w.code_period_value == p])
                          for p in periods}
        c.monthly[key]["TOTAL"] = month_cols(key, wk)
    return c


def bridge(c: Consolidation) -> list[tuple[str, Decimal]]:
    """Cash bridge over the horizon: opening -> flows -> month transitions (residual) -> closing."""
    m = c.monthly
    opening, closing = m["opening"]["TOTAL"], m["closing"]["TOTAL"]
    steps = [("Opening", opening), ("Inflows", m["total_in"]["TOTAL"]), ("Material & projects", -m["mat_out"]["TOTAL"]),
             ("Personnel & other", -m["oth_out"]["TOTAL"])]
    if m["borrowing"]["TOTAL"] != 0:
        steps.append(("Financing", m["borrowing"]["TOTAL"]))
    residual = closing - (opening + sum(v for _, v in steps[1:]))
    if abs(residual) >= Decimal("0.05"):
        steps.append(("Month transitions*", residual))
    steps.append(("Closing", closing))
    return steps
