"""HQ report: consolidated cash-flow forecast of one snapshot (FC CONSO replica, kEUR).

Reads the head revision of every company in a few batched Query Service jobs and consolidates
them with domain/consolidation.py. When the snapshot is FINAL, the head revisions are exactly the
revisions HQ declared final (the snapshot is read-only afterwards).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from domain.calc import calculate, ordered_weeks
from domain.calendar import snapshot_periods
from domain.consolidation import IC_TOLERANCE_KEUR, CompanyFlows, Consolidation, company_flows, consolidate
from domain.ic_partners import with_added_partners
from services.reference_data import ReferenceData
from services.repository import ForecastRepository

CRITICAL, CHECK, INFO = "Critical", "Check", "Info"
_ORDER = {CRITICAL: 0, CHECK: 1, INFO: 2}


@dataclass
class CompanyRow:
    business_unit: str
    name: str
    currency: str
    rate: Decimal | None
    revision: str
    revision_kind: str
    status: str                        # Consolidated | No forecast | No FX rate | No values
    flows: CompanyFlows | None = None


@dataclass
class Report:
    snapshot: str
    status: str                        # FINAL | OPEN
    status_by: str
    status_at: str
    fx_reference: str
    periods: list[str]
    conso: Consolidation | None
    companies: list[CompanyRow] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)

    @property
    def consolidated(self) -> list[CompanyRow]:
        return [c for c in self.companies if c.status == "Consolidated"]


class ReportService:
    def __init__(self, repo: ForecastRepository, ref: ReferenceData):
        self.repo = repo
        self.ref = ref

    def build(self, snapshot: str) -> Report:
        status_row = self.repo.snapshot_status(snapshot)
        states = self.repo.load_states(snapshot)
        ents = self.ref.entities()
        bus = list(ents["CODE_BUSINESS_UNIT"])
        self.ref.prefetch(bus, snapshot)
        names = self.ref.company_names()
        calendar = self.ref.calendar(snapshot)
        weeks = ordered_weeks(calendar)
        rep = Report(snapshot, status_row.get("STATUS") or "OPEN", status_row.get("CREATED_BY", ""),
                     status_row.get("CREATED_AT_UTC", ""), "", snapshot_periods(snapshot), None)
        flows: list[CompanyFlows] = []
        for _, e in ents.iterrows():
            bu, cur = e["CODE_BUSINESS_UNIT"], e["CODE_LOCAL_CURRENCY"]
            rates = self.ref.fx_rates(bu, snapshot)
            rate = Decimal(1) if cur == "EUR" else next(iter(v for v in rates.values() if v is not None), None)
            if not rep.fx_reference:
                ref_txt = self.ref.fx_reference(bu, snapshot)
                rep.fx_reference = "" if ref_txt == "missing" else ref_txt
            st = states.get(bu)
            row = CompanyRow(bu, e["NAME_ENTITY"], cur, rate, f"R{st.head.revision_no}" if st else "",
                             st.head.kind.value if st else "", "No forecast")
            rep.companies.append(row)
            if st is None:
                rep.checks.append(_chk(INFO, bu, "Completeness", "No forecast in this snapshot – not consolidated"))
                continue
            if rate is None:
                row.status = "No FX rate"
                rep.checks.append(_chk(CRITICAL, bu, "Currency",
                                       f"No FX rate for {cur} – company NOT consolidated"))
                continue
            lines = with_added_partners(bu, self.ref.lines(bu), st.labels, names)
            result = calculate(lines, calendar, st.cells)
            cf = company_flows(bu, lines, weeks, result, rate)
            row.flows = cf
            if not cf.has_values:
                row.status = "No values"
                rep.checks.append(_chk(INFO, bu, "Completeness", "No values in the horizon – not consolidated"))
                continue
            row.status = "Consolidated"
            flows.append(cf)
            neg = [(v, w) for w in weeks if (v := cf.closing.get(w.code_week_period)) is not None
                   and v < Decimal("-0.5")]
            if neg:
                low, lw = min(neg, key=lambda x: x[0])
                rep.checks.append(_chk(CRITICAL, bu, "Liquidity",
                                       f"Negative closing cash in {len(neg)} week(s); lowest {low:,.0f} kEUR "
                                       f"({lw.start:%d %b}–{lw.end:%d %b %Y})"))
        if flows:
            rep.conso = consolidate(flows, weeks)
            for p in rep.conso.periods:
                diff = rep.conso.monthly["ic_diff"][p]
                if abs(diff) > IC_TOLERANCE_KEUR:
                    contrib = sorted(((sum(x.weekly["ic_in"][w.code_week_period] - x.weekly["ic_out"][w.code_week_period]
                                           for w in weeks if w.code_period_value == p), x.business_unit) for x in flows),
                                     key=lambda t: -abs(t[0]))
                    top = ", ".join(f"{b} {v:+,.0f}" for v, b in contrib[:4] if abs(v) >= Decimal("0.5"))
                    rep.checks.append(_chk(CHECK, "Group", "Intercompany",
                                           f"{p}: IC inflow − IC outflow = {diff:+,.0f} kEUR (tolerance "
                                           f"±{IC_TOLERANCE_KEUR:,.0f}); largest net IC: {top}"))
        if rep.status != "FINAL":
            rep.checks.append(_chk(INFO, "Group", "Status",
                                   "Snapshot is not FINAL – the report shows the current (still changeable) numbers"))
        rep.checks.sort(key=lambda c: (_ORDER[c["Severity"]], c["Area"]))
        return rep


def _chk(severity: str, area: str, topic: str, message: str) -> dict:
    return {"Severity": severity, "Area": area, "Topic": topic, "Message": message}
