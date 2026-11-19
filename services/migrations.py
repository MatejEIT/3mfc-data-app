"""One-off data migrations, run once per app process at start-up (Keboola backend only).

Each migration is idempotent (a precondition + fixed idempotency keys), so a restart is a no-op.
"""
from __future__ import annotations

import logging

from domain.models import ChangeType, RevisionKind, SubmissionStatus
from services.auth_service import Role
from services.reference_data import ReferenceData
from services.repository import AuditEvent, CommitRequest, ForecastRepository, new_id

log = logging.getLogger(__name__)

BASELINE_SNAPSHOT = "202610"
BASELINE_ACTOR = "Excel baseline (submitted before the app)"
BASELINE_NOTE = ("Excel baseline: the company submitted this forecast in Excel before the app; status set to "
                 "SUBMITTED with unchanged values (one-off data migration approved by HQ)")


def mark_excel_baseline_submitted(repo: ForecastRepository, ref: ReferenceData,
                                  snapshot: str = BASELINE_SNAPSHOT) -> list[str]:
    """Imported Excel forecasts of `snapshot` that are still DRAFT get a SUBMIT revision with exactly the same
    values - no validation, they were submitted in Excel. Later HQ changes then count as HQ adjustments."""
    subs = repo.submissions(snapshot)
    status = dict(zip(subs["CODE_BUSINESS_UNIT"], subs["STATUS"])) if not subs.empty else {}
    currency = dict(zip(ref.entities()["CODE_BUSINESS_UNIT"], ref.entities()["CODE_LOCAL_CURRENCY"]))
    done = []
    for bu, head in sorted(repo.head_revisions(snapshot).items()):
        if head.kind != RevisionKind.IMPORT or status.get(bu) != SubmissionStatus.DRAFT.value:
            continue
        state = repo.load_state(snapshot, bu, head)
        repo.commit(CommitRequest(
            snapshot, bu, str(currency.get(bu, "")), head.revision_no, RevisionKind.SUBMIT, dict(state.cells),
            list(state.ic_details),
            [AuditEvent("", "", "", "", None, None, "", "", ChangeType.ENTITY_SUBMIT, BASELINE_NOTE)],
            BASELINE_ACTOR, Role.HQ, BASELINE_NOTE, f"baseline-submit-{snapshot}-{bu}", new_id(),
            labels=dict(state.labels),
            metric_by_line={c.line_id: c.metric_code for c in state.cells.values()}))
        done.append(bu)
    return done


def run_startup_migrations(repo: ForecastRepository, ref: ReferenceData) -> None:
    try:
        done = mark_excel_baseline_submitted(repo, ref)
        if done:
            log.warning("Migration: %s marked SUBMITTED in %s (Excel baseline)", ", ".join(done), BASELINE_SNAPSHOT)
    except Exception:            # noqa: BLE001 - a migration must never take the app down
        log.exception("Start-up migration failed")
