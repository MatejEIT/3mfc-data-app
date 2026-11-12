"""Forecast editing use cases: open, save draft, submit, HQ adjustment.

All authorisation and validation happens here, on the server, regardless of
what the UI shows or hides.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal

from domain.calc import ForecastResult, calculate, ordered_weeks
from domain.ic_partners import parse_partner_marker, with_added_partners
from domain.metrics import IC_SPLIT_METRIC, METRICS, InputMode, is_nameable, is_placeholder_label
from domain.models import (
    ChangeType,
    IcDetail,
    InputCell,
    Issue,
    Line,
    Provenance,
    RevisionKind,
    Severity,
    SubmissionStatus,
    ValueOrigin,
    ValueState,
    WeekPeriod,
)
from domain.validation import Edit, has_blocking, validate_edits, validate_forecast, validate_label_edits
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
    company_names: dict[str, str] = field(default_factory=dict)

    @property
    def first_week(self) -> str:
        return ordered_weeks(self.calendar)[0].code_week_period

    @property
    def lines_by_id(self) -> dict[str, Line]:
        return {ln.line_id: ln for ln in self.lines}

    def label(self, line: Line) -> str:
        """Entity-typed customer/project name if any, otherwise the template text."""
        override = self.state.labels.get(line.line_id)
        if not override or parse_partner_marker(override):     # IC partner rows keep their generated name
            return line.label
        return override


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
        names = self.ref.company_names()
        calendar = self.ref.calendar(snapshot)
        state = self.repo.load_state(snapshot, bu, revision)
        lines = with_added_partners(bu, self.ref.lines(bu), state.labels, names)
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
            sub.iloc[0]["SUBMITTED_REVISION_ID"] if not sub.empty else "", cps, company_names=names)
        ws.issues = self._validate(ws, state.cells, state.ic_details,
                                   set(state.head.carry_forward_reviewed) if state.head else set())
        return ws

    def _validate(self, ws: Workspace, cells, ic, cf_reviewed: set[str],
                  labels: dict[str, str] | None = None, lines: list[Line] | None = None) -> list[Issue]:
        lines = lines or ws.lines
        result = calculate(lines, ws.calendar, cells)
        issues = validate_forecast(ws.business_unit, lines, ws.calendar, cells, result, ic,
                                   ws.valid_counterparties, {p: v is not None for p, v in ws.fx_rates.items()},
                                   cf_reviewed)
        labels = ws.state.labels if labels is None else labels
        for ln in lines:
            if not is_nameable(ln.metric_code, ln.label):
                continue
            has_value = any(c.state == ValueState.ENTERED for (lid, _), c in cells.items() if lid == ln.line_id)
            if has_value and is_placeholder_label(labels.get(ln.line_id) or ln.label):
                issues.append(Issue(Severity.WARNING, "W_UNNAMED_LINE",
                                    f"A line under '{METRICS[ln.metric_code].label}' has values but no customer / "
                                    "project name", ws.business_unit, line_id=ln.line_id, metric_code=ln.metric_code))
        return issues

    # ------------------------------------------------------------------ save
    def save(self, user: User, ws: Workspace, edits: list[Edit], ic_details: list[IcDetail] | None,
             comment: str, idempotency_key: str, carry_forward_reviewed: set[str] | None = None,
             submit: bool = False, label_edits: dict[str, str] | None = None) -> SaveOutcome:
        submitted = ws.status == SubmissionStatus.SUBMITTED
        user.require_edit(ws.business_unit, submitted)
        if submit and submitted:
            return SaveOutcome(None, [], "Forecast is already submitted.")

        ok_labels, issues = validate_label_edits(label_edits or {}, ws.lines_by_id, ws.business_unit,
                                                 ws.valid_counterparties)
        # IC partners added in this save become real lines before the values are validated
        lines = with_added_partners(ws.business_unit, ws.lines, {**ws.state.labels, **ok_labels}, ws.company_names)
        lines_by_id = {ln.line_id: ln for ln in lines}
        ok, edit_issues = validate_edits(edits, lines_by_id, ws.calendar, ws.business_unit)
        issues += edit_issues
        if has_blocking(issues):
            return SaveOutcome(None, issues, "Nothing saved - fix the highlighted cells.")

        origin = ValueOrigin.HQ_ADJUSTMENT if user.is_hq else ValueOrigin.MANUAL
        change_type = ChangeType.HQ_ADJUSTMENT if user.is_hq else ChangeType.ENTITY_EDIT
        now = utc_now()

        cells = dict(ws.state.cells)
        audit: list[AuditEvent] = []
        for e, value in ok:
            line = lines_by_id[e.line_id]
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

        labels = dict(ws.state.labels)
        for line_id, new_label in ok_labels.items():
            line = lines_by_id[line_id]
            if line.ic_partner and not new_label:
                # remove a partner row the company added: drop the marker and blank its values
                if parse_partner_marker(labels.get(line_id, "")):
                    labels.pop(line_id)
                    for key, c in list(cells.items()):
                        if key[0] == line_id and c.state == ValueState.ENTERED:
                            cells[key] = replace(c, amount=None, state=ValueState.BLANK, origin=origin)
                    audit.append(AuditEvent("", "", line_id, line.metric_code, None, None, "LABEL", "LABEL",
                                            change_type, f"IC partner removed: {line.label}"))
                continue
            if parse_partner_marker(new_label):
                if labels.get(line_id) != new_label:
                    labels[line_id] = new_label
                    audit.append(AuditEvent("", "", line_id, line.metric_code, None, None, "LABEL", "LABEL",
                                            change_type, f"IC partner added: {line.label}"))
                continue
            old_label = ws.label(line)
            if new_label == old_label or (not new_label and line_id not in labels):
                continue
            if new_label:
                labels[line_id] = new_label
            else:
                labels.pop(line_id, None)            # cleared -> back to the template text
            audit.append(AuditEvent("", "", line_id, line.metric_code, None, None, "LABEL", "LABEL", change_type,
                                    f"Name: '{old_label}' → '{new_label or line.label}'"))

        ic = list(ws.state.ic_details) if ic_details is None else list(ic_details)
        audit += self._ic_audit(ws, ic, user)

        cf_reviewed = set(carry_forward_reviewed if carry_forward_reviewed is not None
                          else (ws.state.head.carry_forward_reviewed if ws.state.head else set()))
        prev_cf = set(ws.state.head.carry_forward_reviewed) if ws.state.head else set()

        forecast_issues = self._validate(ws, cells, ic, cf_reviewed, labels, lines)
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
            user.email, user.role, comment, idempotency_key, submission_id, frozenset(cf_reviewed),
            labels=labels, metric_by_line={ln.line_id: ln.metric_code for ln in lines}))
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
