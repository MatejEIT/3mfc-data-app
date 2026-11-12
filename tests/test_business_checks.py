from decimal import Decimal

import pandas as pd
import pytest

from domain import business_checks as BC
from domain.calc import calculate
from domain.models import Severity
from domain.validation import Edit
from services.forecast_service import ForecastService
from services.reference_data import ReferenceData
from services.repository import ForecastRepository
from services.snapshot_service import SnapshotService
from tests.conftest import BU, CAL_202610, TEMPLATE, cell, make_gateway, make_settings

D = Decimal


def test_deviation_needs_a_nonzero_base():
    assert BC.deviation(D(130), D(100)) == D("0.3")
    assert BC.deviation(D(-50), D(-100)) == D("0.5")
    assert BC.deviation(D(5), D(0)) is None and BC.deviation(None, D(1)) is None


def test_months_filled_counts_zero_but_not_opening_cash():
    cells = {c.key: c for c in [cell("L_OPENING_CASH_BALANCE", "202610_W1", 100), cell("L_TAXES", "202610_W2", 0),
                                cell("L_EMPLOYMENT", "202611_W1", 5)]}
    issues = BC.check_months_filled(BU, TEMPLATE, cells, ["202610", "202611", "202612"])
    assert [(i.code, i.period, i.severity) for i in issues] == [("B_MONTH_EMPTY", "202612", Severity.BLOCKING)]


def test_negative_inflow_and_outflow_warn_once_per_line_financing_is_free():
    cells = {c.key: c for c in [cell("L_AR_NOT_OVERDUE_DETAIL", "202610_W1", -5),
                                cell("L_AR_NOT_OVERDUE_DETAIL", "202610_W3", -1),
                                cell("L_TAXES", "202611_W1", -2),
                                cell("L_BANK_FINANCING_MOVEMENT", "202610_W1", -1000)]}
    issues = BC.check_signs(BU, TEMPLATE, cells)
    assert sorted((i.code, i.line_id, i.severity) for i in issues) == [
        ("W_NEGATIVE_INFLOW", "L_AR_NOT_OVERDUE_DETAIL", Severity.WARNING),
        ("W_NEGATIVE_OUTFLOW", "L_TAXES", Severity.WARNING)]
    assert "2 week(s)" in next(i.message for i in issues if i.code == "W_NEGATIVE_INFLOW")


def test_entity_question_only_for_v004():
    assert [i.code for i in BC.entity_questions("V004AE")] == ["Q_ENTITY"]
    assert "factoring" in BC.entity_questions("V004AE")[0].message
    assert BC.entity_questions(BU) == []


def _result(opening):
    cells = {c.key: c for c in [cell("L_OPENING_CASH_BALANCE", "202610_W1", opening)]}
    return calculate(TEMPLATE, CAL_202610, cells)


def test_vs_previous_and_vs_actuals_use_30_percent_limit():
    now = _result(1000)
    assert BC.check_vs_previous(BU, now, _result(800), "202609") == []            # +25 %
    flagged = BC.check_vs_previous(BU, now, _result(700), "202609")              # +43 %
    assert {i.code for i in flagged} == {"W_PREV_SNAPSHOT_DEVIATION"} and all(i.severity == Severity.WARNING
                                                                                 for i in flagged)
    actuals = {f"2026{m:02d}": D(780) for m in range(1, 10)}
    assert BC.check_vs_actuals(BU, now, actuals) == []
    warn = BC.check_vs_actuals(BU, now, {**actuals, "202609": D(100), "202608": D(100), "202607": D(100)})
    assert [i.code for i in warn] == ["W_ACTUALS_DEVIATION"] and "202604–202609" in warn[0].message
    assert [i.code for i in BC.check_vs_actuals(BU, now, None)] == ["I_NO_ACTUALS"]


def _idl(rows):
    return pd.DataFrame([{"CODE_BUSINESS_UNIT": BU, "CODE_FIN_ACCOUNT": acc, "CURRENCY": cur,
                          "CODE_PERIOD_FREQUENCY": "M", "CODE_PLAN_TYPE": "AC", "CODE_PERIOD_VALUE": p,
                          "BALANCE_AMOUNT_YTD": v} for p, acc, cur, v in rows])


def test_cash_actuals_reads_idl_lcy_cash_and_skips_open_months():
    gw = make_gateway()
    gw.sources[("out.c-016-idl-data", "FT_FIN_IDL_VIT_ADS")] = _idl([
        ("2026-08", "12200", "LCY", "1000.5"), ("2026-08", "12200", "LCY", "2000"), ("2026-08", "12200", "EUR", "9"),
        ("2026-08", "40000", "LCY", "777"), ("2026-09", "12200", "LCY", "3500"), ("2026-10", "12200", "LCY", "0"),
        ("2026-11", "12200", "LCY", "1")])
    ref = ReferenceData(gw, make_settings())
    assert ref.cash_actuals(BU, "202611") == {"202608": D("3000.50"), "202609": D("3500.00")}


@pytest.fixture
def svc():
    gw = make_gateway()
    s = make_settings()
    repo, ref = ForecastRepository(gw), ReferenceData(gw, s)
    return gw, repo, ref, ForecastService(repo, ref), SnapshotService(repo, ref)


def test_dry_run_returns_extended_findings_and_saves_nothing(svc, hq):
    gw, repo, ref, fs, ss = svc
    ss.import_initial(hq, "202610", [BU])
    ss.create_rollover(hq, "202611", [BU])
    ws = fs.open(hq, "202611", BU)
    assert not any(i.code == "W_PREV_SNAPSHOT_DEVIATION" for i in ws.issues)     # extended checks not on open
    revs = len(repo.revisions("202611", BU))
    out = fs.save(hq, ws, [Edit("L_AR_NOT_OVERDUE_DETAIL", "202611_W1", "9000000"),
                           Edit("L_TAXES", "202612_W1", "-5")], None, "", "dry", submit=True, dry_run=True)
    codes = {i.code for i in out.issues}
    assert out.revision is None and len(repo.revisions("202611", BU)) == revs
    assert {"W_PREV_SNAPSHOT_DEVIATION", "W_NEGATIVE_OUTFLOW", "I_NO_ACTUALS"} <= codes


def test_submit_records_accepted_findings_in_audit(svc, hq):
    gw, repo, ref, fs, ss = svc
    ss.import_initial(hq, "202610", [BU])
    ws = fs.open(hq, "202610", BU)
    edits = [Edit("L_TAXES", f"{p}_W2", "-1") for p in ("202610", "202611", "202612")]
    found = fs.save(hq, ws, edits, None, "", "v", submit=True, dry_run=True).issues
    assert not any(i.severity == Severity.BLOCKING for i in found)
    out = fs.save(hq, ws, edits, None, "taxes refund expected", "s", submit=True, acknowledged=found)
    assert out.revision is not None
    audit = repo.audit("202610", BU)
    note = audit[audit["CHANGE_TYPE"] == "ENTITY_SUBMIT"]["COMMENT"].iloc[0]
    assert note.startswith("taxes refund expected") and "W_NEGATIVE_OUTFLOW" in note


def test_negative_cash_is_one_finding_in_thousands_with_the_week():
    from domain.validation import validate_forecast
    cells = {c.key: c for c in [cell("L_OPENING_CASH_BALANCE", "202610_W1", 100000),
                                cell("L_TAXES", "202610_W2", 886000)]}
    res = calculate(TEMPLATE, CAL_202610, cells)
    neg = [i for i in validate_forecast(BU, TEMPLATE, CAL_202610, cells, res, [], set(), {}, set())
           if i.code == "W_NEGATIVE_CASH"]
    assert len(neg) == 1 and "-786k" in neg[0].message and neg[0].week == "202610_W2"
    assert "11 week(s)" in neg[0].message


def test_hq_only_findings_hidden_from_entities():
    from domain.models import Issue
    from domain.validation import visible_issues
    found = [Issue(Severity.WARNING, "W_FX_MISSING", "fx"), Issue(Severity.WARNING, "W_CALENDAR_PROPOSED", "cal"),
             Issue(Severity.WARNING, "W_NEGATIVE_CASH", "cash")]
    assert [i.code for i in visible_issues(found, is_hq=False)] == ["W_NEGATIVE_CASH"]
    assert len(visible_issues(found, is_hq=True)) == 3
