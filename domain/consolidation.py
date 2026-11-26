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
from domain.models import Line, RowType, WeekPeriod

ZERO = Decimal(0)
IC_TOLERANCE_KEUR = Decimal(10)

IC_IN_ROWS = {64, 65}                      # OTHER_RECEIPTS_DETAIL rows that are intercompany
OTHER_IN_METRIC = "OTHER_RECEIPTS_DETAIL"
IC_IN_GMBH_ROW = 64                        # "IC (VIDE-GMBH)" receipt; row 65 = "IC (other VI-group)"
IC_OUT_GMBH_METRIC = "INTERCOMPANY_GMBH_OUTFLOW"     # row 88
IC_OUT_OTHER_METRIC = "INTERCOMPANY_OTHER_OUTFLOW"   # row 89
GMBH_BU = "V001DE"                         # VIDE GmbH - the counterparty of rows 64 / 88
GMBH, OTHER = "GMBH", "OTHER"              # IC counterparty buckets (else a partner CODE_BUSINESS_UNIT)

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
    # IC by (direction IN/OUT, counterparty GMBH / partner BU / OTHER) -> week -> kEUR
    ic: dict[tuple[str, str], dict[str, Decimal]] = field(default_factory=dict)
    # detail lines (projects / customers / cost lines) with values: (flow key, label, week -> kEUR); IC excluded
    details: list[tuple[str, str, dict[str, Decimal]]] = field(default_factory=list)


# detail metric -> FLOW key (drill-through of the cash bridge)
DETAIL_KEY = {"AR_NOT_OVERDUE_DETAIL": "ar_not_overdue", "AR_OVERDUE_DETAIL": "ar_overdue",
              "NEW_INVOICE_DETAIL": "new_invoices", OTHER_IN_METRIC: "other_in", "MATERIAL_EXPENSES": "material",
              "LOCAL_PURCHASING": "local_purchasing", "PROJECT_EXPENSE_DETAIL": "project",
              "OTHER_PROJECT_EXPENSES": "project", "EMPLOYMENT": "employment", "INVESTMENTS": "investments",
              "TAXES": "taxes", "OTHER_EXPENSES": "other_exp", "BANK_FINANCING_MOVEMENT": "borrowing"}
# cash-bridge step -> FLOW keys it sums (sign: +1 inflow, -1 outflow)
BRIDGE_KEYS = {"Inflows": (["ar_not_overdue", "ar_overdue", "new_invoices", "other_in"], 1),
               "Material & projects": (["material", "local_purchasing", "project"], -1),
               "Personnel & other": (["employment", "investments", "taxes", "other_exp"], -1),
               "Financing": (["borrowing"], 1)}
KEY_LABEL = {k: lbl for k, lbl, _ in CONSO_ROWS}


def company_flows(bu: str, lines: list[Line], weeks: list[WeekPeriod], result: ForecastResult,
                  rate: Decimal, labels: dict[str, str] | None = None) -> CompanyFlows:
    k = Decimal(1000) * rate
    weekly: dict[str, dict[str, Decimal]] = {key: {} for key in FLOW_KEYS}
    ic_in_lines = [ln.line_id for ln in lines if ln.metric_code == OTHER_IN_METRIC and ln.source_row in IC_IN_ROWS]
    other_in_lines = [ln.line_id for ln in lines if ln.metric_code == OTHER_IN_METRIC
                      and ln.source_row not in IC_IN_ROWS]
    ic_lines: dict[tuple[str, str], list[str]] = {}
    for ln in lines:
        partner = getattr(ln, "ic_partner", "") or ""
        if ln.metric_code == OTHER_IN_METRIC and ln.source_row in IC_IN_ROWS:
            key = ("IN", GMBH if ln.source_row == IC_IN_GMBH_ROW else (partner or OTHER))
        elif ln.metric_code == IC_OUT_GMBH_METRIC:
            key = ("OUT", GMBH)
        elif ln.metric_code == IC_OUT_OTHER_METRIC:
            key = ("OUT", partner or OTHER)
        else:
            continue
        ic_lines.setdefault(key, []).append(ln.line_id)
    ic = {key: {w.code_week_period: sum((_v(result.line_weekly.get((lid, w.code_week_period))) for lid in ids),
                                        ZERO) / k for w in weeks} for key, ids in ic_lines.items()}
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
    details = []
    for ln in lines:
        key = DETAIL_KEY.get(ln.metric_code)
        if key is None or ln.row_type != RowType.DETAIL or (key == "other_in" and ln.source_row in IC_IN_ROWS):
            continue
        series = {w.code_week_period: _v(result.line_weekly.get((ln.line_id, w.code_week_period))) / k for w in weeks}
        if any(series.values()):
            details.append((key, (labels or {}).get(ln.line_id) or ln.label, series))
    return CompanyFlows(bu, rate, weekly, opening, closing, month_closing, any_value, ic, details)


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


# ------------------------------------------------------------------ IC reconciliation
IC_FLOWS = [("to_gmbh", "Subsidiaries → VIDE GmbH"), ("from_gmbh", "VIDE GmbH → subsidiaries"),
            ("between", "Between subsidiaries (other VI-group)")]


def _ic_sum(x: CompanyFlows, direction: str, cps: set[str] | None, wcs: list[str]) -> Decimal:
    """Sum of a company's IC in one direction; cps = counterparties (None = all)."""
    return sum((sum(v.get(w, ZERO) for w in wcs) for (d, cp), v in x.ic.items()
                if d == direction and (cps is None or cp in cps)), ZERO)


def ic_flow_parts(x: CompanyFlows, flow: str, wcs: list[str], gmbh_bu: str = GMBH_BU) -> tuple[Decimal, Decimal]:
    """(received, paid) of one company in one IC flow. Received − paid summed over all companies and flows
    = group IC in − IC out (the 'Check (= 0)' line), so the three flows explain the whole difference."""
    is_g = x.business_unit == gmbh_bu
    if flow == "to_gmbh":
        return (_ic_sum(x, "IN", None, wcs) if is_g else ZERO), (ZERO if is_g else _ic_sum(x, "OUT", {GMBH}, wcs))
    if flow == "from_gmbh":
        return (ZERO if is_g else _ic_sum(x, "IN", {GMBH}, wcs)), (_ic_sum(x, "OUT", None, wcs) if is_g else ZERO)
    if is_g:                                                      # "between": subsidiaries only
        return ZERO, ZERO
    rec = _ic_sum(x, "IN", None, wcs) - _ic_sum(x, "IN", {GMBH}, wcs)
    paid = _ic_sum(x, "OUT", None, wcs) - _ic_sum(x, "OUT", {GMBH}, wcs)
    return rec, paid


def ic_reconciliation(c: Consolidation, gmbh_bu: str = GMBH_BU) -> list[dict]:
    """One row per IC flow: received / paid / difference per period and TOTAL (kEUR)."""
    cols = {p: [w.code_week_period for w in c.weeks if w.code_period_value == p] for p in c.periods}
    cols["TOTAL"] = [w.code_week_period for w in c.weeks]
    rows = []
    for flow, label in IC_FLOWS:
        row = {"flow": flow, "label": label, "received": {}, "paid": {}, "diff": {}}
        for col, wcs in cols.items():
            rec = sum((ic_flow_parts(x, flow, wcs, gmbh_bu)[0] for x in c.companies), ZERO)
            paid = sum((ic_flow_parts(x, flow, wcs, gmbh_bu)[1] for x in c.companies), ZERO)
            row["received"][col], row["paid"][col], row["diff"][col] = rec, paid, rec - paid
        rows.append(row)
    return rows


UNSPEC = "?"            # counterparty not named by the company that booked the amount


@dataclass
class IcMatrix:
    """Who pays whom (kEUR). cells[(payer, receiver)] = {"paid": as booked by the payer,
    "received": as booked by the receiver}. A side that does not name its counterparty is
    booked against UNSPEC (row / column 'not specified')."""
    parties: list[str]                                        # companies with IC, VIDE GmbH first
    cells: dict[tuple[str, str], dict[str, Decimal]]
    assumed: set[tuple[str, str]] = field(default_factory=set)   # cells with a partner from the pair list
    pending: set[str] = field(default_factory=set)   # payers not submitted yet (derived IC inflow: receiver gets nothing)

    def awaiting(self, payer: str, receiver: str) -> bool:
        """Derived IC inflow: the payer has not submitted, so the receiver's inflow is not filled yet."""
        v = self.cell(payer, receiver)
        return payer in self.pending and bool(v["paid"]) and not v["received"]

    def cell(self, payer: str, receiver: str) -> dict[str, Decimal]:
        return self.cells.get((payer, receiver), {"paid": ZERO, "received": ZERO})

    def paid_by(self, payer: str) -> Decimal:
        return sum((v["paid"] for (p, _), v in self.cells.items() if p == payer), ZERO)

    def received_by(self, receiver: str) -> Decimal:
        return sum((v["received"] for (_, r), v in self.cells.items() if r == receiver), ZERO)

    def paid_to(self, receiver: str) -> Decimal:
        return sum((v["paid"] for (_, r), v in self.cells.items() if r == receiver), ZERO)

    def other_side_unnamed(self, payer: str, receiver: str) -> bool:
        """One side of the pair is missing, but that company booked IC without naming the counterparty
        (e.g. VIDE GmbH's receipts) - the amount may be there, it just cannot be attributed."""
        v = self.cell(payer, receiver)
        if v["paid"] and not v["received"]:
            return bool(self.cell(UNSPEC, receiver)["received"])
        if v["received"] and not v["paid"]:
            return bool(self.cell(payer, UNSPEC)["paid"])
        return False

    def mismatches(self, tolerance: Decimal) -> list[dict]:
        """Named pairs whose two sides differ by more than the tolerance. A missing side counts only when
        that company booked no unattributed IC in that direction (else it is a 'not specified' case)."""
        out = [{"payer": p, "receiver": r, "paid": v["paid"], "received": v["received"],
                "diff": v["received"] - v["paid"], "assumed": (p, r) in self.assumed}
               for (p, r), v in self.cells.items() if UNSPEC not in (p, r) and not self.other_side_unnamed(p, r)
               and not self.awaiting(p, r)]
        return sorted((m for m in out if abs(m["diff"]) > tolerance), key=lambda m: -abs(m["diff"]))

    @property
    def unspecified(self) -> tuple[Decimal, Decimal]:
        """(paid without a named receiver, received without a named payer)."""
        received = sum((v["received"] for (p, _), v in self.cells.items() if p == UNSPEC), ZERO)
        return self.paid_to(UNSPEC), received


def ic_matrix(c: Consolidation, wcs: list[str], gmbh_bu: str = GMBH_BU,
              pair_partner: dict[str, str] | None = None, pending: set[str] | None = None) -> IcMatrix:
    """Payer x receiver matrix from the companies' IC rows: 'IC (VIDE-GMBH)' names VIDE GmbH
    (for VIDE GmbH itself the subsidiary is not named), partner rows name the partner,
    'IC (other VI-group) – other / not specified' names nobody - unless the company has exactly one
    partner in the pair list (`pair_partner`, the colour coding of the client's Excel): then that
    partner is assumed and the cell is flagged in `assumed`."""
    cells: dict[tuple[str, str], dict[str, Decimal]] = {}
    assumed: set[tuple[str, str]] = set()
    for x in c.companies:
        me = x.business_unit
        for (direction, cp), series in x.ic.items():
            v = sum((series.get(w, ZERO) for w in wcs), ZERO)
            if v == 0:
                continue
            guess = cp == OTHER and me != gmbh_bu and me in (pair_partner or {})
            if cp == GMBH:
                other = UNSPEC if me == gmbh_bu else gmbh_bu
            elif cp == OTHER:
                other = pair_partner[me] if guess else UNSPEC
            else:
                other = cp
            payer, receiver, side = (me, other, "paid") if direction == "OUT" else (other, me, "received")
            d = cells.setdefault((payer, receiver), {"paid": ZERO, "received": ZERO})
            d[side] += v
            if guess:
                assumed.add((payer, receiver))
    parties = sorted({p for key in cells for p in key if p != UNSPEC}, key=lambda b: (b != gmbh_bu, b))
    return IcMatrix(parties, cells, assumed, set(pending or ()))


def ic_pairs(c: Consolidation, wcs: list[str]) -> list[dict]:
    """Named partner pairs (rows 'IC from / to <partner>'): what A says it pays B vs what B says it receives."""
    by_bu = {x.business_unit: x for x in c.companies}
    out = []
    keys = {((x.business_unit, cp) if d == "OUT" else (cp, x.business_unit))
            for x in c.companies for (d, cp) in x.ic if cp not in (GMBH, OTHER)}
    for a, b in sorted(keys):
        paid = _ic_sum(by_bu[a], "OUT", {b}, wcs) if a in by_bu else ZERO
        received = _ic_sum(by_bu[b], "IN", {a}, wcs) if b in by_bu else ZERO
        if paid or received:
            out.append({"payer": a, "receiver": b, "paid": paid, "received": received, "diff": received - paid})
    return out


# ------------------------------------------------------------------ drill-through & snapshot comparison
def bridge_drill(c: Consolidation, step: str) -> list[dict]:
    """Detail lines behind one cash-bridge step, largest first: company, category, label,
    kEUR per period and TOTAL (sign as in the bridge: outflows negative)."""
    keys, sign = BRIDGE_KEYS[step]
    out = []
    for x in c.companies:
        for key, label, series in x.details:
            if key not in keys:
                continue
            row = {"company": x.business_unit, "key": key, "category": KEY_LABEL.get(key, key), "label": label,
                   "values": {p: sign * sum((series.get(w.code_week_period, ZERO) for w in c.weeks
                                             if w.code_period_value == p), ZERO) for p in c.periods}}
            row["values"]["TOTAL"] = sum(row["values"].values(), ZERO)
            if row["values"]["TOTAL"] or any(row["values"].values()):
                out.append(row)
    return sorted(out, key=lambda r: -abs(r["values"]["TOTAL"]))


COMPARE_ROWS = [("opening", "Opening cash balance"), ("total_in", "Total cash inflow"),
                ("total_out", "Total cash outflow"), ("borrowing", "Borrowing / repayment"),
                ("net", "Net cash movement"), ("closing", "Closing cash balance")]


def compare(cur: Consolidation, prev: Consolidation) -> tuple[list[str], list[dict]]:
    """Months both snapshots forecast and the key group lines of each: current, previous, change (kEUR)."""
    months = [p for p in cur.periods if p in prev.periods]
    rows = []
    for key, label in COMPARE_ROWS:
        vals = {p: (cur.monthly[key][p], prev.monthly[key][p]) for p in months}
        if key == "borrowing" and not any(a or b for a, b in vals.values()):
            continue
        rows.append({"key": key, "label": label,
                     "values": {p: {"cur": a, "prev": b, "diff": a - b} for p, (a, b) in vals.items()}})
    return months, rows


def ic_company_view(c: Consolidation, bu: str, pair_partner: dict[str, str] | None = None,
                    gmbh_bu: str = GMBH_BU) -> dict:
    """IC of one company by partner and month, as the partners book it (Excel 'IC Cash nach Gesellschaft'):
    rows[partner][period] = {"in": what the partner pays to `bu`, "out": what the partner receives from `bu`},
    own[period] = {"in", "out"}: the company's own IC totals (all its IC rows)."""
    me = next((x for x in c.companies if x.business_unit == bu), None)
    rows: dict[str, dict[str, dict[str, Decimal]]] = {}
    own: dict[str, dict[str, Decimal]] = {}
    for p in c.periods:
        wcs = [w.code_week_period for w in c.weeks if w.code_period_value == p]
        m = ic_matrix(c, wcs, gmbh_bu, pair_partner)
        for x in c.companies:
            other = x.business_unit
            if other == bu:
                continue
            rows.setdefault(other, {})[p] = {"in": m.cell(other, bu)["paid"], "out": m.cell(bu, other)["received"]}
        own[p] = {"in": _ic_sum(me, "IN", None, wcs) if me else ZERO,
                  "out": _ic_sum(me, "OUT", None, wcs) if me else ZERO}
    return {"rows": dict(sorted(rows.items())), "own": own}
