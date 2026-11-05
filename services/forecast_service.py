"""Forecast editing use cases: open, save draft, submit, HQ adjustment.

All authorisation and validation happens here, on the server, regardless of
what the UI shows or hides.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal

from domain.calc import ForecastResult, calculate, ordered_weeks
from domain.metrics import IC_SPLIT_METRIC, METRICS, InputMode
from domain.models import (
    ChangeType,
    IcDetail,
    InputCell,
    Issue,
    Line,
    Provenance,
    RevisionKind,
    SubmissionStatus,
    ValueOrigin,
    ValueState,
    WeekPeriod,
)
from domain.validation import Edit, has_blocking, validate_edits, validate_forecast
from services.auth_service import User
from services.reference_data import ReferenceData
from services.repository import AuditEvent, CommitRequest, ForecastRepository, ForecastState, RevisionInfo, new_id, utc_now


@dataclass
class Workspace:
    snapshot: str
    business_unit: str
    entity_name: str
    currency: str
    region: str
    lines: list[Line]
    calendar: list[WeekPeriod]
    state: ForecastState
    result: ForecastResult
    fx_rates: dict[str, Decimal | None]
    fx_reference: str
    status: SubmissionStatus
    submission_id: str
    submitted_revision_id: str
    valid_counterparties: set[str]
    issues: list[Issue] = field(default_factory=list)

    @property
    def first_week(self) -> str:
        return ordered_weeks(self.calendar)[0].code_week_period

    @property
    def lines_by_id(self) -> dict[str, Line]:
        return {ln.line_id: ln for ln in self.lines}


@dataclass
class SaveOutcome:
    revision: RevisionInfo | None
    issues: list[Issue]
    message: str


class ForecastService:
    def __init__(self, repo: ForecastRepository, ref: ReferenceData):
        self.repo = repo
        self.ref = ref

    # ------------------------------------------------------------------ open
    def open(self, user: User, snapshot: str, bu: str, revision: RevisionInfo | None = None) -> Workspace:
        user.require_view(bu)
        ent = self.ref.entities()
        ent = ent[ent["CODE_BUSINESS_UNIT"] == bu].iloc[0]
        lines = self.ref.lines(bu)
        calendar = self.ref.calendar(snapshot)
        state = self.repo.load_state(snapshot, bu, revision)
        result = calculate(lines, calendar, state.cells)
        sub = self.repo.submissions(snapshot)
        sub = sub[sub["CODE_BUSINESS_UNIT"] == bu]
        status = SubmissionStatus(sub.iloc[0]["STATUS"]) if not sub.empty else SubmissionStatus.DRAFT
        cps = set(self.ref.counterparties()["CODE_COUNTERPARTY_ENTITY"])
        fx = self.ref.fx_rates(bu, snapshot)
        ws = Workspace(
            snapshot, bu, str(ent["NAME_ENTITY"]), str(ent["CODE_LOCAL_CURRENCY"]), str(ent["CODE_REGION"]),
            lines, calendar, state, result, fx, self.ref.fx_reference(bu, snapshot), status,
            sub.iloc[0]["SUBMISSION_ID"] if not sub.empty else "",
            sub.iloc[0]["SUBMITTED_REVISION_ID"] if not sub.empty else "", cps)
        ws.issues = self._validate(ws, state.cells, state.ic_details,
                                   set(state.head.carry_forward_reviewed) if state.head else set())
        return ws

    def _validate(self, ws: Workspace, cells, ic, cf_reviewed: set[str]) -> list[Issue]:
        result = calculate(ws.lines, ws.calendar, cells)
        return validate_forecast(ws.business_unit, ws.lines, ws.calendar, cells, result, ic,
                                 ws.valid_counterparties, {p: v is not None for p, v in ws.fx_rates.items()},
                                 cf_reviewed)

    # ------------------------------------------------------------------ save
    def save(self, user: User, ws: Workspace, edits: list[Edit], ic_details: list[IcDetail] | None,
             comment: str, idempotency_key: str, carry_forward_reviewed: set[str] | None = None,
             submit: bool = False) -> SaveOutcome:
        submitted = ws.status == SubmissionStatus.SUBMITTED
        user.require_edit(ws.business_unit, submitted)
        if submit and submitted:
            return SaveOutcome(None, [], "Forecast is already submitted.")

        ok, issues = validate_edits(edits, ws.lines_by_id, ws.calendar, ws.business_unit)
        if has_blocking(issues):
            return SaveOutcome(None, issues, "Nothing saved - fix the highlighted cells.")

        origin = ValueOrigin.HQ_ADJUSTMENT if user.is_hq else ValueOrigin.MANUAL
        change_type = ChangeType.HQ_ADJUSTMENT if user.is_hq else ChangeType.ENTITY_EDIT
        now = utc_now()

        cells = dict(ws.state.cells)
        audit: list[AuditEvent] = []
        for e, value in ok:
            line = ws.lines_by_id[e.line_id]
            old = cells.get((e.line_id, e.week_code))
            mode = METRICS[line.metric_code].input_mode
            if value is None:
                new_state = ValueState.BLANK if mode == InputMode.LINE_INPUT else ValueState.MISSING
            else:
                new_state = ValueState.ENTERED
            if old is not None and old.amount == value and old.state == new_state:
                continue
            period = e.week_code[:6]
            cells[(e.line_id, e.week_code)] = InputCell(
                e.line_id, line.metric_code, period, e.week_code, value, new_state, origin,
                replace(old.provenance, modified_by=user.email, modified_at_utc=now) if old
                else Provenance(ws.snapshot, period, e.week_code, e.line_id, "", user.email, now))
            audit.append(AuditEvent(period, e.week_code, e.line_id, line.metric_code,
                                    old.amount if old else None, value,
                                    old.state.value if old else "", new_state.value, change_type))

        ic = list(ws.state.ic_details) if ic_details is None else list(ic_details)
        audit += self._ic_audit(ws, ic, user)

        cf_reviewed = set(carry_forward_reviewed if carry_forward_reviewed is not None
                          else (ws.state.head.carry_forward_reviewed if ws.state.head else set()))
        prev_cf = set(ws.state.head.carry_forward_reviewed) if ws.state.head else set()

        forecast_issues = self._validate(ws, cells, ic, cf_reviewed)
        if submit and has_blocking(forecast_issues):
            return SaveOutcome(None, forecast_issues, "Submit refused - resolve BLOCKING issues first.")

        if not audit and not submit and cf_reviewed == prev_cf:
            return SaveOutcome(None, forecast_issues, "No changes to save.")

        submission_id = ws.submission_id
        if submit:
            submission_id = new_id()
            audit.append(AuditEvent("", "", "", "", None, None, "", "", ChangeType.ENTITY_SUBMIT,
                                    comment or "Forecast submitted"))
            kind = RevisionKind.SUBMIT
        elif user.is_hq:
            kind = RevisionKind.HQ_ADJUSTMENT
        else:
            kind = RevisionKind.DRAFT_SAVE

        rev = self.repo.commit(CommitRequest(
            ws.snapshot, ws.business_unit, ws.currency, ws.state.revision_no, kind, cells, ic, audit,
            user.email, user.role, comment, idempotency_key, submission_id, frozenset(cf_reviewed)))
        msg = {RevisionKind.SUBMIT: "Forecast submitted", RevisionKind.HQ_ADJUSTMENT: "HQ adjustment saved"}.get(
            kind, "Draft saved")
        return SaveOutcome(rev, forecast_issues, f"{msg} – revision {rev.revision_no}.")

    def _ic_audit(self, ws: Workspace, new_ic: list[IcDetail], user: User) -> list[AuditEvent]:
        old = {d.detail_id: d for d in ws.state.ic_details}
        new = {d.detail_id: d for d in new_ic}
        events = []
        for did in sorted(set(old) | set(new)):
            o, n = old.get(did), new.get(did)
            if o == n:
                continue
            ref = n or o
            desc = []
            if o and n and o.counterparty != n.counterparty:
                desc.append(f"counterparty {o.counterparty} → {n.counterparty}")
            elif n and not o:
                desc.append(f"added {n.counterparty}")
            elif o and not n:
                desc.append(f"removed {o.counterparty}")
            events.append(AuditEvent(ref.code_period_value, ref.code_week_period, did, IC_SPLIT_METRIC,
                                     o.amount if o else None, n.amount if n else None,
                                     "ENTERED" if o else "", "ENTERED" if n else "", ChangeType.IC_CHANGE,
                                     "; ".join(desc)))
        return events

    # ------------------------------------------------------------------ diff
    def diff_revisions(self, ws: Workspace, base: ForecastState, target: ForecastState) -> list[dict]:
        """Cell-level differences, e.g. SUBMITTED vs CURRENT."""
        rows = []
        for key in sorted(set(base.cells) | set(target.cells)):
            a, b = base.cells.get(key), target.cells.get(key)
            av = a.amount if a and a.state == ValueState.ENTERED else None
            bv = b.amount if b and b.state == ValueState.ENTERED else None
            if av == bv:
                continue
            line = ws.lines_by_id.get(key[0])
            rows.append({
                "Week": key[1], "Line": line.label if line else key[0],
                "Metric": METRICS[line.metric_code].label if line else "",
                "Submitted": av, "Current": bv,
                "Difference": (bv or Decimal(0)) - (av or Decimal(0)),
                "Origin": b.origin.value if b else "",
                "Changed by": b.provenance.modified_by if b else "",
            })
        return rows
