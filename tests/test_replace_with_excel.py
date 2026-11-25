"""HQ 'Replace with Excel': real Excel numbers replace a pre-filled snapshot as a new, SUBMITTED revision."""
from decimal import Decimal

import pandas as pd
import pytest

from domain.models import RevisionKind
from services.auth_service import AccessDenied
from tests.conftest import BU, seed_frame

SEED = ("weekly", "FT_3MFC_APP_WEEKLY_INPUT_SEED")


def _add_202611_excel(gateway, opening="5000000", customer="New customer Nov"):
    oct_ = seed_frame()
    nov = oct_[oct_["CODE_PERIOD_VALUE"] != "202610"].copy()
    nov["CODE_SNAPSHOT"] = "202611"
    nov.loc[nov["LINE_ID"] == "L_AR_NOT_OVERDUE_DETAIL", "LINE_DESCRIPTION"] = customer
    first = (nov["LINE_ID"] == "L_OPENING_CASH_BALANCE") & (nov["CODE_WEEK_PERIOD"] == "202611_W1")
    nov.loc[first, "BALANCE_AMOUNT_LCY"] = opening
    gateway.sources[SEED] = pd.concat([nov, oct_], ignore_index=True)     # newer snapshot first on purpose


def test_replace_creates_submitted_revision_and_keeps_history(snapshot_service, repo, ref, gateway, hq):
    snapshot_service.import_initial(hq, "202610", [BU])
    snapshot_service.create_rollover(hq, "202611", [BU])                 # pre-filled R1 (DRAFT)
    _add_202611_excel(gateway)
    ref.invalidate()

    res = snapshot_service.replace_with_excel(hq, "202611", [BU, "V016MX"])
    assert res.created == [BU] and "V016MX" in res.skipped           # no Excel for V016MX

    revs = repo.revisions("202611", BU)
    assert [r.kind for r in revs] == [RevisionKind.ROLLOVER, RevisionKind.SUBMIT]   # R1 stays in history
    sub = repo.submissions("202611").set_index("CODE_BUSINESS_UNIT").loc[BU]
    assert sub["STATUS"] == "SUBMITTED" and sub["SUBMITTED_REVISION_ID"] == revs[-1].revision_id

    state = repo.load_state("202611", BU)
    assert state.cells[("L_OPENING_CASH_BALANCE", "202611_W1")].amount == Decimal("5000000")
    assert state.labels.get("L_AR_NOT_OVERDUE_DETAIL") == "New customer Nov"          # November name
    # template texts stay those of the earliest snapshot -> 202610 forecasts keep their names
    assert next(ln for ln in ref.lines(BU) if ln.line_id == "L_AR_NOT_OVERDUE_DETAIL").label != "New customer Nov"
    assert "Excel" in repo.audit("202611", BU)["COMMENT"].str.cat()


def test_same_excel_twice_is_a_no_op_and_new_excel_adds_a_revision(snapshot_service, repo, ref, gateway, hq):
    snapshot_service.import_initial(hq, "202610", [BU])
    _add_202611_excel(gateway)
    ref.invalidate()
    assert snapshot_service.replace_with_excel(hq, "202611", [BU]).created == [BU]     # no forecast before -> R1
    again = snapshot_service.replace_with_excel(hq, "202611", [BU])
    assert again.created == [] and again.skipped[BU] == "already replaced with this Excel"
    _add_202611_excel(gateway, opening="6000000")
    ref.invalidate()
    assert snapshot_service.replace_with_excel(hq, "202611", [BU]).created == [BU]
    assert len(repo.revisions("202611", BU)) == 2


def test_replace_is_hq_only(snapshot_service, entity_user):
    with pytest.raises(AccessDenied):
        snapshot_service.replace_with_excel(entity_user, "202611", [BU])
