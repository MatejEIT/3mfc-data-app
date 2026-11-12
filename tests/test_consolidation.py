from decimal import Decimal

import pytest

from domain.calc import calculate, ordered_weeks
from domain.consolidation import bridge, company_flows, consolidate
from domain.validation import Edit
from services.repository import SnapshotFinal
from services.report_service import CRITICAL, ReportService
from tests.conftest import BU, CAL_202610, TEMPLATE, cell

D = Decimal
WEEKS = ordered_weeks(CAL_202610)


def _flows(bu, values, rate):
    cells = {c.key: c for c in [cell(lid, wk, v) for (lid, wk), v in values.items()]}
    res = calculate(TEMPLATE, CAL_202610, cells)
    return company_flows(bu, TEMPLATE, WEEKS, res, D(rate))


def test_ic_is_eliminated_and_balances_follow_fc_conso():
    a = _flows("A", {("L_OPENING_CASH_BALANCE", "202610_W1"): 1000, ("L_AR_NOT_OVERDUE_DETAIL", "202610_W1"): 500,
                     ("L_TAXES", "202610_W2"): 200, ("L_INTERCOMPANY_OTHER_OUTFLOW", "202610_W1"): 100}, 1)
    b = _flows("B", {("L_OPENING_CASH_BALANCE", "202610_W1"): 4400, ("L_IC_OTHER_IN", "202610_W1"): 440}, "4.4")
    c = consolidate([a, b], WEEKS)
    w1, w2 = "202610_W1", "202610_W2"
    assert c.weekly["total_in"][w1] == D("0.5")                 # 500 LCY = 0.5 kEUR; IC not in inflow
    assert c.weekly["ic_in"][w1] == D("0.1") and c.weekly["ic_out"][w1] == D("0.1")
    assert c.weekly["ic_diff"][w1] == 0
    assert c.weekly["opening"][w1] == D(2)                      # 1 kEUR + 4400 LCY / 4.4 / 1000
    assert c.weekly["net"][w1] == D("0.5") and c.weekly["closing"][w1] == D("2.5")
    assert c.weekly["opening"][w2] == D("2.5")                  # chained within the month
    assert c.monthly["taxes"]["202610"] == D("0.2")
    assert c.monthly["opening"]["202610"] == D(2) and c.monthly["closing"]["202610"] == D("2.3")
    # next month is re-based on the companies' own openings (incl. IC) -> equals here because IC nets to 0
    assert c.weekly["opening"]["202611_W1"] == a.opening["202611_W1"] + b.opening["202611_W1"]
    labels = [s for s, _ in bridge(c)]
    assert labels[0] == "Opening" and labels[-1] == "Closing"
    assert sum(v for _, v in bridge(c)[1:-1]) + bridge(c)[0][1] == bridge(c)[-1][1]


def test_unbalanced_ic_shows_up_as_month_transition():
    a = _flows("A", {("L_OPENING_CASH_BALANCE", "202610_W1"): 1000, ("L_INTERCOMPANY_OTHER_OUTFLOW", "202610_W4"): 300}, 1)
    c = consolidate([a], WEEKS)
    assert c.monthly["ic_diff"]["202610"] == D("-0.3")
    assert c.monthly["closing"]["202610"] == D(1)               # group ignores IC within the month
    assert c.weekly["opening"]["202611_W1"] == D("0.7")         # ...and re-bases on the company opening
    assert dict(bridge(c))["Month transitions*"] == D("-0.3")


@pytest.fixture
def snap(snapshot_service, hq):
    snapshot_service.import_initial(hq, "202610", [BU])
    return "202610"


def test_final_snapshot_is_read_only_and_reopen_unlocks(snap, snapshot_service, forecast_service, repo, hq):
    assert snapshot_service.finalize(hq, snap, "October final") == [BU]
    assert repo.snapshot_status(snap)["STATUS"] == "FINAL" and repo.final_snapshots() == [snap]
    audit = repo.audit(snap, BU)
    ev = audit[audit["CHANGE_TYPE"] == "SNAPSHOT_STATUS"].iloc[0]
    assert "revision 1 is the final one" in ev["COMMENT"] and "October final" in ev["COMMENT"]
    ws = forecast_service.open(hq, snap, BU)
    assert ws.final
    from services.auth_service import AccessDenied
    with pytest.raises(AccessDenied):
        forecast_service.save(hq, ws, [Edit("L_TAXES", "202610_W1", "1")], None, "", "x")
    ws.final = False                                            # stale page: the commit itself refuses too
    with pytest.raises(SnapshotFinal):
        forecast_service.save(hq, ws, [Edit("L_TAXES", "202610_W1", "1")], None, "", "y")
    snapshot_service.reopen(hq, snap, "late correction")
    ws = forecast_service.open(hq, snap, BU)
    assert not ws.final and forecast_service.save(hq, ws, [Edit("L_TAXES", "202610_W1", "1")], None, "",
                                                  "z").revision is not None


def test_entity_cannot_finalize(snap, snapshot_service, entity_user):
    from services.auth_service import AccessDenied
    with pytest.raises(AccessDenied):
        snapshot_service.finalize(entity_user, snap)


def test_report_consolidates_companies_with_rate_and_lists_the_rest(snap, repo, ref, gateway):
    jobs = gateway.jobs
    rep = ReportService(repo, ref).build(snap)
    by_bu = {x.business_unit: x for x in rep.companies}
    assert by_bu[BU].status == "Consolidated" and by_bu[BU].rate == D("1.1")
    assert by_bu["V001DE"].status == "No forecast" and by_bu["V016MX"].status == "No forecast"
    c = rep.conso
    assert c.monthly["opening"]["202610"] == (D(3131000) / D("1.1") / 1000)
    assert c.monthly["total_in"]["202610"] == D(200000) / D("1.1") / 1000
    assert rep.status == "OPEN" and any(ch["Topic"] == "Status" for ch in rep.checks)
    assert gateway.jobs - jobs <= 8                             # batched: not one job per company
    assert not any(ch["Severity"] == CRITICAL for ch in rep.checks)
