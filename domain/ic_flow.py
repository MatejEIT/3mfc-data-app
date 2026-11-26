"""Derived IC inflow (from snapshot IC_DERIVED_FROM on).

Every company enters only the IC money it SENDS (outflow): row 88 "IC (VIDE-GMBH)" = payment to
VIDE GmbH, partner rows "IC to <company>" under row 89 = payment to that company. Its IC INFLOW is not
entered - it is derived from what the other companies send to it:

* source = the latest revision of a payer whose forecast is SUBMITTED (HQ adjustments included);
  a payer that has not submitted yet is 'pending' - its payments are NOT in the receiver's inflow
  (missing is not zero, nothing is invented),
* currency: payer LCY -> EUR (payer rate) -> receiver LCY (receiver rate), the snapshot rates of the
  consolidation; without a rate the amount is left out and reported ('FX missing'),
* timing: same week as the payment (no transit time).

Receiver rows: a subsidiary gets VIDE GmbH's payments in row 64 "IC (VIDE-GMBH)"; every other payer
gets a read-only partner row "IC from <payer>" under row 65. The rows are calculated (never stored,
never editable). Snapshots before IC_DERIVED_FROM keep the IC inflow the companies entered.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal

from domain.ic_partners import add_partner_rows, partner_line_id
from domain.models import InputCell, Issue, Line, Provenance, Severity, ValueOrigin, ValueState

IC_DERIVED_FROM = "202612"
GMBH_BU = "V001DE"
IC_IN_METRIC = "OTHER_RECEIPTS_DETAIL"
IC_IN_ROWS = (64, 65)
IC_IN_GMBH_ROW = 64
IC_OUT_GMBH_METRIC = "INTERCOMPANY_GMBH_OUTFLOW"     # row 88
IC_OUT_OTHER_METRIC = "INTERCOMPANY_OTHER_OUTFLOW"   # row 89 (+ partner rows)
CENT = Decimal("0.01")


def is_derived(snapshot: str) -> bool:
    return str(snapshot) >= IC_DERIVED_FROM


def is_inflow_line(ln: Line) -> bool:
    return ln.metric_code == IC_IN_METRIC and ln.source_row in IC_IN_ROWS


@dataclass
class PayerOutflow:
    """IC a submitted company sends, by receiver and week (payer LCY)."""
    business_unit: str
    revision_id: str
    currency: str
    rates: dict[str, Decimal | None]                     # period -> LCY per EUR
    to: dict[str, dict[str, Decimal]] = field(default_factory=dict)   # receiver -> week -> LCY


def payer_outflow(bu: str, revision_id: str, currency: str, rates: dict[str, Decimal | None],
                  cells: dict, all_bus: list[str], gmbh_bu: str = GMBH_BU) -> PayerOutflow:
    """Outflow by receiver from a payer's input cells: row 88 -> VIDE GmbH, partner rows -> the partner.
    The 'IC (other VI-group)' row without a partner names nobody and is not routed."""
    partner_of = {partner_line_id(bu, "OUT", r): r for r in all_bus if r != bu}
    out = PayerOutflow(bu, revision_id, currency, rates)
    for c in cells.values():
        if c.state != ValueState.ENTERED or not c.amount:
            continue
        if c.metric_code == IC_OUT_GMBH_METRIC and bu != gmbh_bu:
            receiver = gmbh_bu
        elif c.metric_code == IC_OUT_OTHER_METRIC and c.line_id in partner_of:
            receiver = partner_of[c.line_id]
        else:
            continue
        wk = out.to.setdefault(receiver, {})
        wk[c.code_week_period] = wk.get(c.code_week_period, Decimal(0)) + c.amount
    return out


def _rate(currency: str, rates: dict[str, Decimal | None], period: str) -> Decimal | None:
    return Decimal(1) if currency == "EUR" else rates.get(period)


@dataclass
class DerivedInflow:
    lines: list[Line]                                   # receiver lines incl. read-only inflow rows
    cells: dict[tuple[str, str], InputCell]             # calculated inflow cells (receiver LCY)
    sources: list[tuple[str, str, Decimal]]             # (payer, revision id, total receiver LCY)
    fx_missing: list[str]                               # payers whose amounts could not be converted
    pending: list[str]                                  # companies that have not submitted yet


def derive_inflow(receiver: str, currency: str, rates: dict[str, Decimal | None], lines: list[Line],
                  payers: list[PayerOutflow], pending: list[str], names: dict[str, str],
                  gmbh_bu: str = GMBH_BU) -> DerivedInflow:
    """Receiver lines with read-only inflow rows and their calculated cells."""
    base = [ln for ln in lines if not (is_inflow_line(ln) and ln.ic_partner)]   # entered partner IN rows go
    paying = sorted(p.business_unit for p in payers if receiver in p.to and p.business_unit != receiver)
    row_payers = [b for b in paying if not (b == gmbh_bu and receiver != gmbh_bu)]
    out_lines = add_partner_rows(receiver, base, {"IN": row_payers}, names) if row_payers else base
    out_lines = [replace(ln, derived=True) if is_inflow_line(ln) else ln for ln in out_lines]
    row64 = next((ln for ln in out_lines if is_inflow_line(ln) and not ln.ic_partner
                  and ln.source_row == IC_IN_GMBH_ROW), None)
    cells: dict[tuple[str, str], InputCell] = {}
    sources, fx_missing = [], []
    for p in sorted(payers, key=lambda x: x.business_unit):
        weeks = p.to.get(receiver)
        if not weeks or p.business_unit == receiver:
            continue
        if p.business_unit == gmbh_bu and receiver != gmbh_bu:
            line_id = row64.line_id if row64 else None
        else:
            line_id = partner_line_id(receiver, "IN", p.business_unit)
        if line_id is None:
            continue
        total, missing = Decimal(0), False
        for week, amount in sorted(weeks.items()):
            period = week[:6]
            r_pay, r_rcv = _rate(p.currency, p.rates, period), _rate(currency, rates, period)
            if r_pay is None or r_rcv is None:
                missing = True
                continue
            v = amount if p.currency == currency else (amount / r_pay * r_rcv).quantize(CENT)
            cells[(line_id, week)] = InputCell(        # one line per payer -> keys are unique
                line_id, IC_IN_METRIC, period, week, v, ValueState.ENTERED, ValueOrigin.CALCULATED,
                Provenance(source_revision_id=p.revision_id, source_line_id=p.business_unit))
            total += v
        if missing:
            fx_missing.append(p.business_unit)
        sources.append((p.business_unit, p.revision_id, total))
    return DerivedInflow(out_lines, cells, sources, fx_missing, sorted(b for b in pending if b != receiver))


def inflow_issues(bu: str, d: DerivedInflow) -> list[Issue]:
    out = []
    if d.fx_missing:
        out.append(Issue(Severity.WARNING, "W_IC_INFLOW_FX",
                         "IC inflow from " + ", ".join(d.fx_missing) + " cannot be converted to your currency – "
                         "FX rate missing; these amounts are not in your inflow yet", bu))
    if d.pending:
        out.append(Issue(Severity.INFO, "I_IC_INFLOW_PENDING",
                         f"IC inflow is filled from the outflow of the companies that submitted; waiting for "
                         f"{len(d.pending)}: " + ", ".join(d.pending), bu))
    return out


def outflow_issues(bu: str, lines: list[Line], cells: dict, gmbh_bu: str = GMBH_BU) -> list[Issue]:
    """Every IC payment must name its receiver (derived mode): no amount on 'IC (other VI-group)'
    without a partner; VIDE GmbH pays the subsidiaries through 'IC to <company>' rows, not row 88."""
    by_id = {ln.line_id: ln for ln in lines}
    out = []
    for lid in sorted({lid for (lid, _), c in cells.items() if c.state == ValueState.ENTERED and c.amount}):
        ln = by_id.get(lid)
        if ln is None:
            continue
        if ln.metric_code == IC_OUT_OTHER_METRIC and not ln.ic_partner:
            out.append(Issue(Severity.BLOCKING, "E_IC_NO_PARTNER",
                             "IC outflow without a receiver: move the amounts of 'IC (other VI-group)' to a row "
                             "'IC to <company>' (＋ add IC partner) – the receiver's IC inflow is filled from it",
                             bu, line_id=lid, metric_code=ln.metric_code))
        elif ln.metric_code == IC_OUT_GMBH_METRIC and bu == gmbh_bu:
            out.append(Issue(Severity.BLOCKING, "E_IC_GMBH_ROW88",
                             "VIDE GmbH pays the subsidiaries through rows 'IC to <company>' under "
                             "'IC (other VI-group)' – row 'IC (VIDE-GMBH)' must stay empty", bu,
                             line_id=lid, metric_code=ln.metric_code))
    return out


def move_to_single_partner(bu: str, lines: list[Line], cells: dict, partner: str | None
                           ) -> tuple[dict, list[str]]:
    """Pre-fill from the pair list: amounts on 'IC (other VI-group)' outflow without a partner go to the
    row 'IC to <partner>' when the company has exactly one partner and that row is empty in the week.
    Returns the new cells and the moved week codes."""
    if not partner:
        return cells, []
    base = next((ln for ln in lines if ln.metric_code == IC_OUT_OTHER_METRIC and not ln.ic_partner), None)
    target = partner_line_id(bu, "OUT", partner)
    if base is None or target not in {ln.line_id for ln in lines}:
        return cells, []
    out, moved = dict(cells), []
    for (lid, wk), c in cells.items():
        if lid != base.line_id or c.state != ValueState.ENTERED or not c.amount:
            continue
        t = out.get((target, wk))
        if t is not None and t.state == ValueState.ENTERED and t.amount:
            continue
        out[(target, wk)] = replace(c, line_id=target)
        out[(lid, wk)] = replace(c, amount=None, state=ValueState.BLANK)
        moved.append(wk)
    return out, sorted(moved)
