from decimal import Decimal

import pytest

from domain.calc import calculate, ordered_weeks
from domain.consolidation import bridge, company_flows, consolidate, ic_pairs, ic_reconciliation
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


def test_ic_reconciliation_splits_the_difference_by_flow_and_pairs():
    import domain.consolidation as C
    g = _flows("V001DE", {("L_OPENING_CASH_BALANCE", "202610_W1"): 1000,
                          ("L_IC_OTHER_IN", "202610_W1"): 300,                 # GmbH receives 300 from subsidiaries
                          ("L_INTERCOMPANY_OTHER_OUTFLOW", "202610_W2"): 50}, 1)   # GmbH pays 50 to subsidiaries
    a = _flows("A", {("L_OPENING_CASH_BALANCE", "202610_W1"): 1000,
                     ("L_INTERCOMPANY_GMBH_OUTFLOW", "202610_W1"): 200,          # A says it pays GmbH 200
                     ("L_INTERCOMPANY_OTHER_OUTFLOW", "202610_W1"): 40}, 1)     # A pays B 40 (unspecified)
    b = _flows("B", {("L_OPENING_CASH_BALANCE", "202610_W1"): 1000,
                     ("L_IC_OTHER_IN", "202610_W1"): 30}, 1)                     # B receives 30
    c = consolidate([g, a, b], WEEKS)
    rec = {r["flow"]: r for r in ic_reconciliation(c)}
    assert rec["to_gmbh"]["diff"]["TOTAL"] == D("0.1")         # GmbH 300 received vs 200 paid by subsidiaries
    assert rec["from_gmbh"]["diff"]["TOTAL"] == D("-0.05")     # GmbH paid 50, nobody booked it as received
    assert rec["between"]["diff"]["TOTAL"] == D("-0.01")       # 30 received vs 40 paid
    assert sum(r["diff"]["TOTAL"] for r in rec.values()) == c.monthly["ic_diff"]["TOTAL"]
    # named partner rows -> pair view
    a.ic[("OUT", "B")] = a.ic.pop(("OUT", C.OTHER))
    b.ic[("IN", "A")] = b.ic.pop(("IN", C.OTHER))
    pairs = ic_pairs(consolidate([g, a, b], WEEKS), [w.code_week_period for w in WEEKS])
    assert pairs == [{"payer": "A", "receiver": "B", "paid": D("0.04"), "received": D("0.03"), "diff": D("-0.01")}]


def test_ic_matrix_payer_by_receiver():
    import domain.consolidation as C
    g = _flows("V001DE", {("L_OPENING_CASH_BALANCE", "202610_W1"): 1000,
                          ("L_IC_OTHER_IN", "202610_W1"): 300,                  # GmbH: received, payer not named
                          ("L_INTERCOMPANY_OTHER_OUTFLOW", "202610_W2"): 50}, 1)
    a = _flows("A", {("L_OPENING_CASH_BALANCE", "202610_W1"): 1000,
                     ("L_INTERCOMPANY_GMBH_OUTFLOW", "202610_W1"): 200,           # A -> GmbH (named by the template)
                     ("L_INTERCOMPANY_OTHER_OUTFLOW", "202610_W1"): 40}, 1)
    b = _flows("B", {("L_OPENING_CASH_BALANCE", "202610_W1"): 1000, ("L_IC_OTHER_IN", "202610_W1"): 30}, 1)
    a.ic[("OUT", "B")] = a.ic.pop(("OUT", C.OTHER))                                # A names B as partner
    b.ic[("IN", "A")] = b.ic.pop(("IN", C.OTHER))
    c = consolidate([g, a, b], WEEKS)
    m = C.ic_matrix(c, [w.code_week_period for w in WEEKS])
    assert m.parties == ["V001DE", "A", "B"]                                       # VIDE GmbH first
    assert m.cell("A", "V001DE") == {"paid": D("0.2"), "received": D(0)}
    assert m.cell(C.UNSPEC, "V001DE")["received"] == D("0.3")
    assert m.cell("V001DE", C.UNSPEC)["paid"] == D("0.05")
    assert m.cell("A", "B") == {"paid": D("0.04"), "received": D("0.03")}
    assert m.unspecified == (D("0.05"), D("0.3"))
    # Σ received − Σ paid over the whole matrix = the group IC check line
    total = sum((v["received"] - v["paid"] for v in m.cells.values()), D(0))
    assert total == c.monthly["ic_diff"]["TOTAL"]
    # A -> GmbH: GmbH books its receipts without naming the payer -> not a pair mismatch, just unattributed
    assert m.other_side_unnamed("A", "V001DE")
    assert [(x["payer"], x["receiver"]) for x in m.mismatches(D("0.005"))] == [("A", "B")]
    assert m.mismatches(D(1)) == []
    from components.report_ui import ic_matrix_table
    html_ = ic_matrix_table(m, {"A": "Alpha"}, D("0.005"))
    assert "Payer ↓" in html_ and "not spec." in html_ and 'class="c bad"' in html_ and "V001 does not name" in html_
    # a partner that books nothing at all -> one-sided (amber) and a mismatch
    b.ic.pop(("IN", "A"))
    m2 = C.ic_matrix(consolidate([g, a, b], WEEKS), [w.code_week_period for w in WEEKS])
    assert not m2.other_side_unnamed("A", "B") and ("A", "B") in [(x["payer"], x["receiver"]) for x in m2.mismatches(D(0))]
    assert 'class="c one"' in ic_matrix_table(m2, {}, D("0.005"))


def test_ic_without_partner_warns_the_company():
    from domain.business_checks import check_ic_partner
    from domain.models import Line, RowType
    base = Line("X_IC89", "INTERCOMPANY_OTHER_OUTFLOW", RowType.DETAIL, "IC (other VI-group)", 89)
    partner = Line("X_P", "INTERCOMPANY_OTHER_OUTFLOW", RowType.DETAIL, "IC to V016MX", 89, ic_partner="V016MX")
    cells = {c.key: c for c in [cell("X_IC89", "202610_W2", 5000), cell("X_P", "202610_W1", 7000)]}
    issues = check_ic_partner(BU, [base, partner], cells)
    assert [i.code for i in issues] == ["W_IC_NO_PARTNER"] and issues[0].line_id == "X_IC89"
    assert "IC paid without a partner company" in issues[0].message


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
    assert by_bu["V001DE"].status == "Missing" and by_bu["V016MX"].status == "Missing"
    c = rep.conso
    assert c.monthly["opening"]["202610"] == (D(3131000) / D("1.1") / 1000)
    assert c.monthly["total_in"]["202610"] == D(200000) / D("1.1") / 1000
    assert rep.status == "OPEN" and any(ch["Topic"] == "Status" for ch in rep.checks)
    assert gateway.jobs - jobs <= 8                             # batched: not one job per company
    crit = {ch["Area"] for ch in rep.checks if ch["Severity"] == CRITICAL}
    assert crit == {"V001DE", "V016MX"}                          # expected but missing


def test_not_expected_companies_are_info_and_rates_grouped_by_currency(snap, repo, ref, gateway):
    import pandas as pd
    ents = gateway.sources[("fx", "DIM_3MFC_ENTITY")]
    gateway.sources[("fx", "DIM_3MFC_ENTITY")] = pd.concat([ents, pd.DataFrame([{
        "CODE_BUSINESS_UNIT": "V013DE", "CODE_ENTITY": "V013", "NAME_ENTITY": "Dormant", "COUNTRY": "DE",
        "CODE_REGION": "WEE", "CODE_LOCAL_CURRENCY": "EUR"}])], ignore_index=True)
    ref.invalidate()
    rep = ReportService(repo, ref).build(snap)
    v013 = next(x for x in rep.companies if x.business_unit == "V013DE")
    assert v013.status == "Not expected" and not v013.expected
    assert next(ch for ch in rep.checks if ch["Area"] == "V013DE")["Severity"] == "Info"
    rates = {r["Currency"]: r for r in rep.rates_by_currency()}
    assert rates["USD"]["Rate"] == D("1.1") and rates["USD"]["Used"] and rates["USD"]["Companies"] == [BU]
    assert rates["EUR"]["Rate"] == D(1) and set(rates["EUR"]["Companies"]) == {"V001DE", "V013DE"}



def test_ic_matrix_uses_the_pair_list_for_unnamed_other_vi_group():
    import domain.consolidation as C
    from domain.ic_partners import single_partners
    bus = {"V002": "V002US", "V016": "V016MX", "V008": "V008PL", "V017": "V017EE", "V010": "V010FR"}
    one = single_partners(bus)
    assert one["V002US"] == "V016MX" and one["V016MX"] == "V002US"
    assert "V008PL" not in one and one["V017EE"] == "V008PL"            # V008 has two partners -> ambiguous
    a = _flows("V002US", {("L_OPENING_CASH_BALANCE", "202610_W1"): 1000,
                          ("L_INTERCOMPANY_OTHER_OUTFLOW", "202610_W1"): 40}, 1)   # Excel: no counterparty
    b = _flows("V016MX", {("L_OPENING_CASH_BALANCE", "202610_W1"): 1000, ("L_IC_OTHER_IN", "202610_W1"): 40}, 1)
    p = _flows("V008PL", {("L_OPENING_CASH_BALANCE", "202610_W1"): 1000,
                          ("L_INTERCOMPANY_OTHER_OUTFLOW", "202610_W1"): 10}, 1)
    c = consolidate([a, b, p], WEEKS)
    wcs = [w.code_week_period for w in WEEKS]
    m = C.ic_matrix(c, wcs, pair_partner=one)
    assert m.cell("V002US", "V016MX") == {"paid": D("0.04"), "received": D("0.04")}
    assert ("V002US", "V016MX") in m.assumed and m.mismatches(D(0)) == []
    assert m.cell("V008PL", C.UNSPEC)["paid"] == D("0.01")              # ambiguous stays not specified
    assert C.ic_matrix(c, wcs).cell("V002US", C.UNSPEC)["paid"] == D("0.04")   # without the list: unnamed
    from components.report_ui import ic_matrix_table
    assert 'class="c as ok"' in ic_matrix_table(m, {}, D("0.005"))
