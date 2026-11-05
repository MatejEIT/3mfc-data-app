"""Revision-based persistence with optimistic locking.

Commit protocol (Storage API has no multi-table transactions):

  0. in-process lock per (snapshot, entity) - Data Apps run one container,
     so this serialises concurrent sessions of the same app
  1. idempotency: a COMMITTED revision with the same IDEMPOTENCY_KEY is
     returned instead of writing again (double click / rerun safe)
  2. optimistic lock: caller's BASE_REVISION_NO must equal the committed head,
     otherwise RevisionConflict (someone saved in the meantime)
  3. REVISION row written as PENDING with a fresh WRITE_ID, read back and
     verified
  4. full input state, IC split and audit events written, tagged by WRITE_ID
  5. REVISION row flipped to COMMITTED and verified again

Readers only ever use COMMITTED revisions and only rows whose WRITE_ID equals
that revision's WRITE_ID, so a crash between steps leaves an ignored PENDING
revision and orphan rows - never a half-applied state.
"""
from __future__ import annotations

import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import pandas as pd

from domain.models import (
    ChangeType,
    IcDetail,
    InputCell,
    Provenance,
    RevisionKind,
    Role,
    SubmissionStatus,
    ValueOrigin,
    ValueState,
)
from services import schema as S
from services.keboola_client import Gateway

_LOCKS: dict[tuple[str, str], threading.Lock] = defaultdict(threading.Lock)
_LOCKS_GUARD = threading.Lock()


def _lock_for(snapshot: str, bu: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS[(snapshot, bu)]


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_id() -> str:
    return uuid.uuid4().hex


class RevisionConflict(RuntimeError):
    """The forecast changed since it was loaded - reload and re-apply."""


def fmt_amount(v: Decimal | None) -> str:
    return "" if v is None else format(v, "f")


def parse_stored(v: str) -> Decimal | None:
    return None if v in ("", None) else Decimal(v)


@dataclass(frozen=True)
class RevisionInfo:
    revision_id: str
    revision_no: int
    kind: RevisionKind
    write_id: str
    created_by: str
    created_role: str
    created_at_utc: str
    comment: str
    submission_id: str
    carry_forward_reviewed: frozenset[str]


@dataclass(frozen=True)
class AuditEvent:
    period: str
    week: str
    line_id: str
    metric_code: str
    old_value: Decimal | None
    new_value: Decimal | None
    old_state: str
    new_state: str
    change_type: ChangeType
    comment: str = ""


@dataclass
class ForecastState:
    snapshot: str
    business_unit: str
    head: RevisionInfo | None
    cells: dict[tuple[str, str], InputCell] = field(default_factory=dict)
    ic_details: list[IcDetail] = field(default_factory=list)

    @property
    def revision_no(self) -> int:
        return self.head.revision_no if self.head else 0


@dataclass(frozen=True)
class CommitRequest:
    snapshot: str
    business_unit: str
    currency: str
    base_revision_no: int
    kind: RevisionKind
    cells: dict[tuple[str, str], InputCell]
    ic_details: list[IcDetail]
    audit: list[AuditEvent]
    actor: str
    role: Role
    comment: str
    idempotency_key: str
    submission_id: str = ""
    carry_forward_reviewed: frozenset[str] = frozenset()


def _rev_from_row(r: pd.Series) -> RevisionInfo:
    cf = frozenset(p for p in str(r.get("CARRY_FORWARD_REVIEWED", "")).split(",") if p)
    return RevisionInfo(r["REVISION_ID"], int(r["REVISION_NO"]), RevisionKind(r["REVISION_KIND"]), r["WRITE_ID"],
                        r["CREATED_BY"], r["CREATED_ROLE"], r["CREATED_AT_UTC"], r["COMMENT"],
                        r["SUBMISSION_ID"], cf)


class ForecastRepository:
    def __init__(self, gateway: Gateway, clock: Callable[[], str] = utc_now, ids: Callable[[], str] = new_id):
        self.gw = gateway
        self.clock = clock
        self.ids = ids

    # ------------------------------------------------------------------ reads
    def revisions(self, snapshot: str, bu: str, committed_only: bool = True) -> list[RevisionInfo]:
        where: dict = {"CODE_SNAPSHOT": snapshot, "CODE_BUSINESS_UNIT": bu}
        if committed_only:
            where["STATUS"] = "COMMITTED"
        df = self.gw.read_app(S.REVISION, where)
        revs = [_rev_from_row(r) for _, r in df.iterrows()]
        return sorted(revs, key=lambda r: r.revision_no)

    def head(self, snapshot: str, bu: str) -> RevisionInfo | None:
        revs = self.revisions(snapshot, bu)
        return revs[-1] if revs else None

    def load_state(self, snapshot: str, bu: str, revision: RevisionInfo | None = None) -> ForecastState:
        rev = revision or self.head(snapshot, bu)
        state = ForecastState(snapshot, bu, rev)
        if rev is None:
            return state
        for _, r in self.gw.read_app(S.INPUT, {"WRITE_ID": rev.write_id}).iterrows():
            cell = InputCell(
                r["LINE_ID"], r["METRIC_CODE"], r["CODE_PERIOD_VALUE"], r["CODE_WEEK_PERIOD"],
                parse_stored(r["AMOUNT_LCY"]), ValueState(r["VALUE_STATE"]), ValueOrigin(r["VALUE_ORIGIN"]),
                Provenance(r["SOURCE_SNAPSHOT"], r["SOURCE_PERIOD"], r["SOURCE_WEEK"], r["SOURCE_LINE_ID"],
                           r["SOURCE_REVISION_ID"], r["MODIFIED_BY"], r["MODIFIED_AT_UTC"]))
            state.cells[cell.key] = cell
        for _, r in self.gw.read_app(S.IC_DETAIL, {"WRITE_ID": rev.write_id}).iterrows():
            state.ic_details.append(IcDetail(r["DETAIL_ID"], r["CODE_PERIOD_VALUE"], r["CODE_WEEK_PERIOD"],
                                             r["CODE_COUNTERPARTY_ENTITY"], Decimal(r["AMOUNT_LCY"] or "0"),
                                             r["COMMENT"], ValueOrigin(r["VALUE_ORIGIN"])))
        return state

    def audit(self, snapshot: str | None = None, bu: str | None = None) -> pd.DataFrame:
        where = {}
        if snapshot:
            where["CODE_SNAPSHOT"] = snapshot
        if bu:
            where["CODE_BUSINESS_UNIT"] = bu
        df = self.gw.read_app(S.AUDIT, where or None)
        committed = self.gw.read_app(S.REVISION, {**where, "STATUS": "COMMITTED"} if where else {"STATUS": "COMMITTED"})
        return df[df["WRITE_ID"].isin(committed["WRITE_ID"])]  # hide events of aborted writes

    def snapshots(self) -> list[str]:
        df = self.gw.read_app(S.REVISION, {"STATUS": "COMMITTED"})
        return sorted(set(df["CODE_SNAPSHOT"]), reverse=True)

    def submissions(self, snapshot: str) -> pd.DataFrame:
        return self.gw.read_app(S.SUBMISSION, {"CODE_SNAPSHOT": snapshot})

    # ----------------------------------------------------------------- writes
    def commit(self, req: CommitRequest) -> RevisionInfo:
        with _lock_for(req.snapshot, req.business_unit):
            return self._commit_locked(req)

    def _commit_locked(self, req: CommitRequest) -> RevisionInfo:
        all_revs = self.gw.read_app(S.REVISION, {"CODE_SNAPSHOT": req.snapshot,
                                                 "CODE_BUSINESS_UNIT": req.business_unit})
        if req.idempotency_key:
            dup = all_revs[(all_revs["IDEMPOTENCY_KEY"] == req.idempotency_key) & (all_revs["STATUS"] == "COMMITTED")]
            if not dup.empty:
                return _rev_from_row(dup.iloc[0])
        committed = all_revs[all_revs["STATUS"] == "COMMITTED"]
        head_no = int(committed["REVISION_NO"].astype(int).max()) if not committed.empty else 0
        if req.base_revision_no != head_no:
            raise RevisionConflict(
                f"Forecast {req.snapshot}/{req.business_unit} is at revision {head_no}, "
                f"your changes are based on revision {req.base_revision_no}. Reload and re-apply.")

        rev_no = head_no + 1
        rev_id = f"{req.snapshot}-{req.business_unit}-R{rev_no:05d}"
        write_id = self.ids()
        now = self.clock()
        rev_row = {
            "REVISION_ID": rev_id, "CODE_SNAPSHOT": req.snapshot, "CODE_BUSINESS_UNIT": req.business_unit,
            "REVISION_NO": str(rev_no), "BASE_REVISION_NO": str(head_no), "REVISION_KIND": req.kind.value,
            "STATUS": "PENDING", "WRITE_ID": write_id, "IDEMPOTENCY_KEY": req.idempotency_key,
            "SUBMISSION_ID": req.submission_id, "CHANGE_COUNT": str(len(req.audit)), "CREATED_BY": req.actor,
            "CREATED_ROLE": req.role.value, "CREATED_AT_UTC": now, "COMMITTED_AT_UTC": "",
            "CALC_STATUS": "CALCULATED_IN_APP", "COMMENT": req.comment,
            "CARRY_FORWARD_REVIEWED": ",".join(sorted(req.carry_forward_reviewed)),
        }
        self.gw.upsert_app(S.REVISION, pd.DataFrame([rev_row]))
        self._verify(rev_id, write_id, "PENDING")

        base = {"WRITE_ID": write_id, "REVISION_ID": rev_id, "CODE_SNAPSHOT": req.snapshot,
                "CODE_BUSINESS_UNIT": req.business_unit}
        self.gw.upsert_app(S.INPUT, pd.DataFrame([{
            **base, "CODE_PERIOD_VALUE": c.code_period_value, "CODE_WEEK_PERIOD": c.code_week_period,
            "LINE_ID": c.line_id, "METRIC_CODE": c.metric_code, "AMOUNT_LCY": fmt_amount(c.amount),
            "CODE_CURRENCY": req.currency, "VALUE_STATE": c.state.value, "VALUE_ORIGIN": c.origin.value,
            "SOURCE_SNAPSHOT": c.provenance.source_snapshot, "SOURCE_PERIOD": c.provenance.source_period,
            "SOURCE_WEEK": c.provenance.source_week, "SOURCE_LINE_ID": c.provenance.source_line_id,
            "SOURCE_REVISION_ID": c.provenance.source_revision_id, "MODIFIED_BY": c.provenance.modified_by,
            "MODIFIED_AT_UTC": c.provenance.modified_at_utc,
        } for c in req.cells.values()]))
        if req.ic_details:
            self.gw.upsert_app(S.IC_DETAIL, pd.DataFrame([{
                **base, "DETAIL_ID": d.detail_id, "CODE_PERIOD_VALUE": d.code_period_value,
                "CODE_WEEK_PERIOD": d.code_week_period, "METRIC_CODE": "INTERCOMPANY_OTHER_OUTFLOW",
                "CODE_COUNTERPARTY_ENTITY": d.counterparty, "AMOUNT_LCY": fmt_amount(d.amount),
                "CODE_CURRENCY": req.currency, "VALUE_ORIGIN": d.origin.value, "COMMENT": d.comment,
                "MODIFIED_BY": req.actor, "MODIFIED_AT_UTC": now,
            } for d in req.ic_details]))
        if req.audit:
            self.gw.upsert_app(S.AUDIT, pd.DataFrame([{
                "EVENT_ID": self.ids(), "WRITE_ID": write_id, "SUBMISSION_ID": req.submission_id,
                "REVISION_ID": rev_id, "CODE_SNAPSHOT": req.snapshot, "CODE_BUSINESS_UNIT": req.business_unit,
                "CODE_PERIOD_VALUE": e.period, "CODE_WEEK_PERIOD": e.week, "LINE_ID": e.line_id,
                "METRIC_CODE": e.metric_code, "OLD_VALUE_LCY": fmt_amount(e.old_value),
                "NEW_VALUE_LCY": fmt_amount(e.new_value), "OLD_VALUE_STATE": e.old_state,
                "NEW_VALUE_STATE": e.new_state, "CODE_CURRENCY": req.currency, "CHANGE_TYPE": e.change_type.value,
                "CHANGED_BY": req.actor, "CHANGED_ROLE": req.role.value, "CHANGED_AT_UTC": now,
                "COMMENT": e.comment or req.comment,
            } for e in req.audit]))

        rev_row.update({"STATUS": "COMMITTED", "COMMITTED_AT_UTC": self.clock()})
        self.gw.upsert_app(S.REVISION, pd.DataFrame([rev_row]))
        self._verify(rev_id, write_id, "COMMITTED")
        info = _rev_from_row(pd.Series(rev_row))
        self._update_submission(req, info)
        return info

    def _verify(self, rev_id: str, write_id: str, status: str) -> None:
        df = self.gw.read_app(S.REVISION, {"REVISION_ID": rev_id})
        if df.empty or df.iloc[0]["WRITE_ID"] != write_id or df.iloc[0]["STATUS"] != status:
            raise RevisionConflict(f"Revision {rev_id} was taken over by a concurrent save. Reload and re-apply.")

    def _update_submission(self, req: CommitRequest, rev: RevisionInfo) -> None:
        cur = self.gw.read_app(S.SUBMISSION, {"CODE_SNAPSHOT": req.snapshot, "CODE_BUSINESS_UNIT": req.business_unit})
        row = cur.iloc[0].to_dict() if not cur.empty else {
            "CODE_SNAPSHOT": req.snapshot, "CODE_BUSINESS_UNIT": req.business_unit,
            "STATUS": SubmissionStatus.DRAFT.value, "SUBMISSION_ID": "", "SUBMITTED_REVISION_ID": "",
            "SUBMITTED_BY": "", "SUBMITTED_AT_UTC": "", "HAS_HQ_ADJUSTMENTS": "0", "NOTIFIED_REVISION_ID": "",
            "NOTIFICATION_PENDING": "0",
        }
        row["CURRENT_REVISION_ID"] = rev.revision_id
        row["CALC_STATUS"] = "CURRENT"
        row["UPDATED_AT_UTC"] = rev.created_at_utc
        if req.kind == RevisionKind.SUBMIT:
            row.update({"STATUS": SubmissionStatus.SUBMITTED.value, "SUBMISSION_ID": req.submission_id,
                        "SUBMITTED_REVISION_ID": rev.revision_id, "SUBMITTED_BY": req.actor,
                        "SUBMITTED_AT_UTC": rev.created_at_utc})
        if req.kind == RevisionKind.HQ_ADJUSTMENT and row["STATUS"] == SubmissionStatus.SUBMITTED.value:
            row["HAS_HQ_ADJUSTMENTS"] = "1"
            row["NOTIFICATION_PENDING"] = "1"
        self.gw.upsert_app(S.SUBMISSION, pd.DataFrame([row]))
