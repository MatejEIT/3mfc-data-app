"""End-to-end use cases against the in-memory backend (same semantics as Keboola upserts)."""
from decimal import Decimal

import pytest

from domain.models import IcDetail, RevisionKind, SubmissionStatus, ValueOrigin, ValueState, month_slot
from domain.validation import Edit
from services import schema as S
from services.auth_service import AccessDenied
from services.keboola_client import StorageWriteError
from services.repository import RevisionConflict
from tests.conftest import BU, ar_ap_ids

D = Decimal


@pytest.fixture
def imported(snapshot_service, hq):
    res = snapshot_service.import_initial(hq, "202610", [BU, "V001DE"])
    assert res.created == [BU]
    assert res.skipped == {"V001DE": "no Excel seed for this entity"}
    return res


def _complete_edits() -> list[Edit]:
    edits = [Edit(lid, month_slot(p), "1000") for lid in ar_ap_ids() for p in ("202610", "202611", "202612")]
    edits += [Edit("L_TAXES", f"{p}_W2", "0") for p in ("202610", "202611", "202612")]   # every month has values
    return edits


def _ic_split() -> list[IcDetail]:
    return [IcDetail("ic1", "202610", "202610_W2", "V001DE", D(100))]


def test_import_keeps_blank_vs_zero(imported, forecast_service, hq):
    ws = forecast_service.open(hq, "202610", BU)
    assert ws.state.revision_no == 1
    assert ws.state.cells[("L_EMPLOYMENT", "202612_W4")].state == ValueState.ENTERED
    assert ws.state.cells[("L_EMPLOYMENT", "202612_W4")].amount == 0
    assert ws.state.cells[("L_EMPLOYMENT", "202610_W1")].state == ValueState.BLANK
    assert ws.state.cells[(ar_ap_ids()[0], month_slot("202610"))].state == ValueState.MISSING
    assert ("L_OPENING_CASH_BALANCE", "202611_W1") not in ws.state.cells  # calculated, not stored


def test_import_is_not_repeated(imported, snapshot_service, hq):
    again = snapshot_service.import_initial(hq, "202610", [BU])
    assert again.created == [] and again.skipped == {BU: "forecast already exists"}


def test_entity_save_draft_creates_revision_and_audit(imported, forecast_service, repo, entity_user):
    ws = forecast_service.open(entity_user, "202610", BU)
    out = forecast_service.save(entity_user, ws, [Edit("L_EMPLOYMENT", "202610_W1", "250")], None, "payroll", "k1")
    assert out.revision.revision_no == 2 and out.revision.kind == RevisionKind.DRAFT_SAVE
    audit = repo.audit("202610", BU)
    ev = audit[audit["CHANGE_TYPE"] == "ENTITY_EDIT"].iloc[0]
    assert (ev["OLD_VALUE_LCY"], ev["NEW_VALUE_LCY"], ev["OLD_VALUE_STATE"]) == ("", "250", "BLANK")
    assert ev["CHANGED_BY"] == entity_user.email and ev["COMMENT"] == "payroll"
    ws2 = forecast_service.open(entity_user, "202610", BU)
    assert ws2.result.metric_weekly[("EMPLOYMENT", "202610_W1")].amount == D(250)


def test_submit_blocked_until_required_data_complete(imported, forecast_service, entity_user):
    ws = forecast_service.open(entity_user, "202610", BU)
    out = forecast_service.save(entity_user, ws, [Edit("L_OPENING_CASH_BALANCE", "202610_W1", "")], None, "",
                                "s1", submit=True)
    assert out.revision is None
    codes = {i.code for i in out.issues if i.severity.value == "BLOCKING"}
    assert "B_OPENING_MISSING" in codes
    # AR/AP balances are postponed (AR_AP_ENABLED = False) and IC is entered per partner row
    assert "B_AR_AP_MISSING" not in codes and "B_IC_UNALLOCATED" not in codes


def test_full_submit_then_hq_adjustment_keeps_submitted_revision_immutable(imported, forecast_service, repo,
                                                                           entity_user, hq):
    ws = forecast_service.open(entity_user, "202610", BU)
    out = forecast_service.save(entity_user, ws, _complete_edits(), _ic_split(), "complete", "s2", submit=True)
    assert out.revision is not None, [i.message for i in out.issues]
    assert out.revision.kind == RevisionKind.SUBMIT

    ws = forecast_service.open(entity_user, "202610", BU)
    assert ws.status == SubmissionStatus.SUBMITTED
    with pytest.raises(AccessDenied):
        forecast_service.save(entity_user, ws, [Edit("L_TAXES", "202610_W1", "1")], None, "", "x")

    ws_hq = forecast_service.open(hq, "202610", BU)
    adj = forecast_service.save(hq, ws_hq, [Edit("L_TAXES", "202610_W1", "777")], None, "tax correction", "h1")
    assert adj.revision.kind == RevisionKind.HQ_ADJUSTMENT

    sub = repo.submissions("202610").iloc[0]
    assert sub["STATUS"] == "SUBMITTED" and sub["HAS_HQ_ADJUSTMENTS"] == "1" and sub["NOTIFICATION_PENDING"] == "1"
    assert sub["CURRENT_REVISION_ID"] == adj.revision.revision_id != sub["SUBMITTED_REVISION_ID"]

    submitted_rev = next(r for r in repo.revisions("202610", BU) if r.revision_id == sub["SUBMITTED_REVISION_ID"])
    original = repo.load_state("202610", BU, submitted_rev)
    current = repo.load_state("202610", BU)
    assert original.cells[("L_TAXES", "202610_W1")].state == ValueState.BLANK
    assert current.cells[("L_TAXES", "202610_W1")].amount == D(777)
    assert current.cells[("L_TAXES", "202610_W1")].origin == ValueOrigin.HQ_ADJUSTMENT
    diff = forecast_service.diff_revisions(ws_hq, original, current)
    assert len(diff) == 1 and diff[0]["Difference"] == D(777)

    audit = repo.audit("202610", BU)
    hq_ev = audit[audit["CHANGE_TYPE"] == "HQ_ADJUSTMENT"].iloc[0]
    assert hq_ev["CHANGED_ROLE"] == "HQ" and hq_ev["COMMENT"] == "tax correction"
    assert (audit["CHANGE_TYPE"] == "ENTITY_SUBMIT").sum() == 1
    assert (audit["CHANGE_TYPE"] == "IC_CHANGE").sum() == 1


def test_concurrent_edit_is_rejected(imported, forecast_service, entity_user, hq):
    a = forecast_service.open(entity_user, "202610", BU)
    b = forecast_service.open(hq, "202610", BU)
    forecast_service.save(entity_user, a, [Edit("L_TAXES", "202610_W1", "1")], None, "", "a")
    with pytest.raises(RevisionConflict):
        forecast_service.save(hq, b, [Edit("L_TAXES", "202610_W1", "2")], None, "", "b")


def test_double_click_is_idempotent(imported, forecast_service, repo, entity_user):
    ws = forecast_service.open(entity_user, "202610", BU)
    r1 = forecast_service.save(entity_user, ws, [Edit("L_TAXES", "202610_W1", "1")], None, "", "same-key").revision
    r2 = forecast_service.save(entity_user, ws, [Edit("L_TAXES", "202610_W1", "1")], None, "", "same-key").revision
    assert r1.revision_id == r2.revision_id
    assert len(repo.revisions("202610", BU)) == 2


def test_failed_write_leaves_previous_revision_current(imported, forecast_service, repo, gateway, entity_user):
    ws = forecast_service.open(entity_user, "202610", BU)

    def boom(table, df):
        if table is S.AUDIT:
            raise StorageWriteError("network")
    gateway.before_write = boom
    with pytest.raises(StorageWriteError):
        forecast_service.save(entity_user, ws, [Edit("L_TAXES", "202610_W1", "9")], None, "", "f")
    gateway.before_write = None
    assert repo.head("202610", BU).revision_no == 1
    assert repo.load_state("202610", BU).cells[("L_TAXES", "202610_W1")].state == ValueState.BLANK
    ok = forecast_service.save(entity_user, ws, [Edit("L_TAXES", "202610_W1", "9")], None, "", "f2")
    assert ok.revision.revision_no == 2


def test_entity_cannot_see_other_entity(imported, forecast_service, other_entity_user):
    with pytest.raises(AccessDenied):
        forecast_service.open(other_entity_user, "202610", BU)


def test_entity_cannot_create_snapshots(snapshot_service, entity_user):
    with pytest.raises(AccessDenied):
        snapshot_service.create_rollover(entity_user, "202611", [BU])


def test_calculated_cell_edit_rejected_server_side(imported, forecast_service, hq):
    ws = forecast_service.open(hq, "202610", BU)
    out = forecast_service.save(hq, ws, [Edit("L_NET_CASH_MOVEMENT", "202610_W1", "1")], None, "", "n")
    assert out.revision is None and out.issues[0].code == "E_NOT_EDITABLE"


def test_rolling_2_plus_1(imported, forecast_service, snapshot_service, repo, entity_user, hq):
    ws = forecast_service.open(entity_user, "202610", BU)
    forecast_service.save(entity_user, ws, _complete_edits() + [
        Edit("L_TAXES", "202612_W3", "42"), Edit("L_TAXES", "202611_W1", "7")], _ic_split(), "", "pre")
    closing_oct = forecast_service.open(hq, "202610", BU).result.metric_weekly[
        ("CLOSING_CASH_BALANCE", "202610_W4")].amount

    res = snapshot_service.create_rollover(hq, "202611", [BU])
    assert res.created == [BU]
    nov = repo.load_state("202611", BU)
    c = nov.cells
    assert c[("L_TAXES", "202611_W1")].amount == D(7) and c[("L_TAXES", "202611_W1")].origin == ValueOrigin.ROLLOVER
    assert c[("L_TAXES", "202612_W3")].amount == D(42)
    jan = c[("L_TAXES", "202701_W3")]
    assert jan.amount == D(42) and jan.origin == ValueOrigin.CARRY_FORWARD
    assert jan.provenance.source_week == "202612_W3" and jan.provenance.source_snapshot == "202610"
    assert c[("L_EMPLOYMENT", "202701_W1")].state == ValueState.BLANK          # blank stays blank
    assert c[("L_OPENING_CASH_BALANCE", "202611_W1")].amount == closing_oct   # cash continuity
    assert c[(ar_ap_ids()[0], month_slot("202701"))].origin == ValueOrigin.CARRY_FORWARD
    assert {i.code for i in res.issues} >= {"I_CARRY_FORWARD", "W_CALENDAR_PROPOSED"}

    ws_nov = forecast_service.open(entity_user, "202611", BU)
    cf = [i for i in ws_nov.issues if i.code == "W_CARRY_FORWARD_UNREVIEWED"]
    assert cf and all(i.severity.value == "WARNING" for i in cf)          # a reminder, never blocks the submit
    # snapshot 202610 history is untouched
    assert repo.head("202610", BU).revision_no == 2
