"""Snapshot lifecycle: initial Excel import and rolling 2+1 creation (HQ only)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from decimal import Decimal

import pandas as pd

from domain.calc import calculate, ordered_weeks
from domain.calendar import next_period
from domain.metrics import METRICS, InputMode
from domain.models import (
    ChangeType,
    InputCell,
    Issue,
    Line,
    Provenance,
    RevisionKind,
    Severity,
    ValueOrigin,
    ValueState,
    WeekPeriod,
    month_slot,
)
from domain.rolling import build_rollover
from services.auth_service import User
from services.reference_data import ReferenceData
from services.repository import AuditEvent, CommitRequest, ForecastRepository, new_id, utc_now


@dataclass
class SnapshotRunResult:
    created: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)


def cells_from_seed(bu: str, snapshot: str, lines: list[Line], calendar: list[WeekPeriod], seed: pd.DataFrame,
                    actor: str, now: str) -> dict[tuple[str, str], InputCell]:
    """Excel seed -> input cells. Empty Excel cells stay BLANK, never 0."""
    first = ordered_weeks(calendar)[0].code_week_period
    week_codes = {w.code_week_period for w in calendar}
    by_key = {(r["LINE_ID"], r["CODE_WEEK_PERIOD"]): r for _, r in seed.iterrows()}
    cells: dict[tuple[str, str], InputCell] = {}
    for ln in lines:
        mode = METRICS[ln.metric_code].input_mode
        if mode == InputMode.LINE_INPUT:
            targets = sorted(week_codes)
        elif mode == InputMode.OPENING_FIRST:
            targets = [first]
        elif mode == InputMode.MONTH_STATE_INPUT:
            for p in sorted({w.code_period_value for w in calendar}):
                cells[(ln.line_id, month_slot(p))] = InputCell(
                    ln.line_id, ln.metric_code, p, month_slot(p), None, ValueState.MISSING, ValueOrigin.EXCEL_IMPORT,
                    Provenance(snapshot, p, "", ln.line_id, "", actor, now))
            continue
        else:
            continue
        for wk in targets:
            r = by_key.get((ln.line_id, wk))
            raw = "" if r is None else str(r["BALANCE_AMOUNT_LCY"]).strip()
            if raw in ("", "nan", "None"):
                state = ValueState.MISSING if mode == InputMode.OPENING_FIRST else ValueState.BLANK
                amount = None
            else:
                state, amount = ValueState.ENTERED, Decimal(raw)
            cells[(ln.line_id, wk)] = InputCell(
                ln.line_id, ln.metric_code, wk[:6], wk, amount, state, ValueOrigin.EXCEL_IMPORT,
                Provenance(snapshot, wk[:6], wk, ln.line_id, "", actor, now))
    return cells


class SnapshotService:
    def __init__(self, repo: ForecastRepository, ref: ReferenceData):
        self.repo = repo
        self.ref = ref

    def import_initial(self, user: User, snapshot: str, business_units: list[str],
                       on_progress: Callable[[int, int, str], None] | None = None) -> SnapshotRunResult:
        """Create revision 1 of `snapshot` from the Excel seed. Never overwrites an existing forecast."""
        user.require_hq()
        res = SnapshotRunResult()
        calendar = self.ref.calendar(snapshot)
        ents = self.ref.entities().set_index("CODE_BUSINESS_UNIT")
        for i, bu in enumerate(business_units, 1):
            if on_progress:
                on_progress(i, len(business_units), bu)
            if self.repo.head(snapshot, bu) is not None:
                res.skipped[bu] = "forecast already exists"
                continue
            seed = self.ref.seed_rows(bu, snapshot)
            if seed.empty:
                res.skipped[bu] = "no Excel seed for this entity"
                continue
            lines = self.ref.lines(bu)
            now = utc_now()
            cells = cells_from_seed(bu, snapshot, lines, calendar, seed, user.email, now)
            self.repo.commit(CommitRequest(
                snapshot, bu, str(ents.loc[bu, "CODE_LOCAL_CURRENCY"]), 0, RevisionKind.IMPORT, cells, [],
                [AuditEvent("", "", "", "", None, None, "", "", ChangeType.SYSTEM_CORRECTION,
                            f"Initial import from Excel seed ({len(cells)} input cells)")],
                user.email, user.role, "Initial Excel import", f"import-{snapshot}-{bu}"))
            res.created.append(bu)
        return res

    def create_rollover(self, user: User, new_snapshot: str, business_units: list[str],
                        on_progress: Callable[[int, int, str], None] | None = None) -> SnapshotRunResult:
        """Rolling 2+1 from the latest COMMITTED revision of the previous snapshot."""
        user.require_hq()
        prev_snapshot = next_period(new_snapshot, -1)
        prev_cal, new_cal = self.ref.calendar(prev_snapshot), self.ref.calendar(new_snapshot)
        ents = self.ref.entities().set_index("CODE_BUSINESS_UNIT")
        res = SnapshotRunResult()
        for i, bu in enumerate(business_units, 1):
            if on_progress:
                on_progress(i, len(business_units), bu)
            if self.repo.head(new_snapshot, bu) is not None:
                res.skipped[bu] = "snapshot already exists"
                continue
            prev = self.repo.load_state(prev_snapshot, bu)
            if prev.head is None:
                res.skipped[bu] = f"no committed forecast in {prev_snapshot}"
                continue
            lines = self.ref.lines(bu)
            prev_result = calculate(lines, prev_cal, prev.cells)
            now = utc_now()
            roll = build_rollover(
                business_unit=bu, prev_snapshot=prev_snapshot, new_snapshot=new_snapshot,
                source_revision_id=prev.head.revision_id, lines=lines, prev_calendar=prev_cal,
                new_calendar=new_cal, prev_cells=prev.cells, prev_result=prev_result, prev_ic=prev.ic_details,
                actor=user.email, now_utc=now)
            audit = [AuditEvent(p, "", "", "", None, None, "", "",
                                ChangeType.CARRY_FORWARD if origin == ValueOrigin.CARRY_FORWARD else ChangeType.ROLLOVER,
                                f"{origin.value} from {prev_snapshot} revision {prev.head.revision_id}")
                     for p, origin in sorted({(c.code_period_value, c.origin) for c in roll.cells.values()})]
            self.repo.commit(CommitRequest(
                new_snapshot, bu, str(ents.loc[bu, "CODE_LOCAL_CURRENCY"]), 0, RevisionKind.ROLLOVER, roll.cells,
                roll.ic_details, audit, user.email, user.role,
                f"Rolling 2+1 from {prev.head.revision_id}", f"rollover-{new_snapshot}-{bu}",
                labels=dict(prev.labels), metric_by_line={ln.line_id: ln.metric_code for ln in lines}))
            res.created.append(bu)
            res.issues += roll.issues
        if any(w.needs_confirmation for w in new_cal):
            res.issues.append(Issue(Severity.WARNING, "W_CALENDAR_PROPOSED",
                                    "New month calendar is a proposal - confirm it in Admin › Calendar"))
        return res

    def new_detail_id(self) -> str:
        return new_id()
