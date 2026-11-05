from decimal import Decimal

from domain.calc import calculate, is_cell_editable, to_eur
from domain.models import InputCell, Line, RowType, ValueOrigin, ValueState, month_slot
from tests.conftest import CAL_202610, TEMPLATE, cell, v002_october_w1_cells

D = Decimal


def by_id(i):
    return next(ln for ln in TEMPLATE if ln.line_id == i)


def test_reproduces_excel_kpis_v002_october_w1():
    r = calculate(TEMPLATE, CAL_202610, v002_october_w1_cells())
    w = "202610_W1"
    assert r.metric_weekly[("TOTAL_CASH_INFLOW", w)].amount == D(450000)
    assert r.metric_weekly[("TOTAL_CASH_OUTFLOW", w)].amount == D(1443000)
    assert r.metric_weekly[("NET_CASH_MOVEMENT", w)].amount == D(-993000)
    assert r.metric_weekly[("OPENING_CASH_BALANCE", w)].amount == D(3131000)
    assert r.metric_weekly[("CLOSING_CASH_BALANCE", w)].amount == D(2138000)


def test_opening_chains_from_previous_closing_across_weeks_and_months():
    r = calculate(TEMPLATE, CAL_202610, v002_october_w1_cells())
    weeks = [w.code_week_period for w in CAL_202610]
    for prev, cur in zip(weeks, weeks[1:]):
        assert r.metric_weekly[("OPENING_CASH_BALANCE", cur)] == r.metric_weekly[("CLOSING_CASH_BALANCE", prev)]


def test_weekly_to_monthly_aggregation_rules():
    cells = v002_october_w1_cells()
    cells.update({c.key: c for c in [cell("L_EMPLOYMENT", "202610_W2", 10), cell("L_EMPLOYMENT", "202610_W4", 5)]})
    r = calculate(TEMPLATE, CAL_202610, cells)
    assert r.metric_monthly[("EMPLOYMENT", "202610")].amount == D(15)
    assert r.metric_monthly[("TOTAL_CASH_OUTFLOW", "202610")].amount == D(1443015)
    assert r.metric_monthly[("OPENING_CASH_BALANCE", "202610")] == r.metric_weekly[("OPENING_CASH_BALANCE", "202610_W1")]
    assert r.metric_monthly[("CLOSING_CASH_BALANCE", "202610")] == r.metric_weekly[("CLOSING_CASH_BALANCE", "202610_W4")]
    # monthly closing = opening + sum of weekly net movements
    m = r.metric_monthly
    assert m[("CLOSING_CASH_BALANCE", "202610")].amount == (
        m[("OPENING_CASH_BALANCE", "202610")].amount + m[("NET_CASH_MOVEMENT", "202610")].amount)


def test_detail_lines_of_same_metric_are_summed():
    cells = {c.key: c for c in [cell("L_AR_NOT_OVERDUE_DETAIL", "202610_W1", 1),
                                cell("L_AR_NOT_OVERDUE_DETAIL_2", "202610_W1", 2),
                                cell("L_OPENING_CASH_BALANCE", "202610_W1", 0)]}
    r = calculate(TEMPLATE, CAL_202610, cells)
    assert r.metric_weekly[("AR_NOT_OVERDUE_COLLECTIONS", "202610_W1")].amount == D(3)
    assert r.line_weekly[("L_AR_NOT_OVERDUE_COLLECTIONS", "202610_W1")].amount == D(3)


def test_missing_opening_is_unknown_not_zero():
    cells = v002_october_w1_cells()
    del cells[("L_OPENING_CASH_BALANCE", "202610_W1")]
    r = calculate(TEMPLATE, CAL_202610, cells)
    assert r.metric_weekly[("OPENING_CASH_BALANCE", "202610_W1")].amount is None
    assert r.metric_weekly[("CLOSING_CASH_BALANCE", "202612_W4")].amount is None
    assert r.metric_weekly[("NET_CASH_MOVEMENT", "202610_W1")].amount == D(-993000)


def test_blank_and_explicit_zero_are_distinguished():
    cells = {c.key: c for c in [cell("L_TAXES", "202610_W1", 0),
                                cell("L_INVESTMENTS", "202610_W1", None, ValueState.BLANK)]}
    r = calculate(TEMPLATE, CAL_202610, cells)
    assert r.line_weekly[("L_TAXES", "202610_W1")].has_input is True
    assert r.line_weekly[("L_INVESTMENTS", "202610_W1")].has_input is False
    assert r.line_weekly[("L_TAXES", "202610_W1")].amount == r.line_weekly[("L_INVESTMENTS", "202610_W1")].amount == 0


def test_ar_ap_month_state_not_split_into_weeks():
    ar = Line("AR", "AR_CLOSING_BALANCE", RowType.BALANCE, "AR", 99)
    slot = month_slot("202610")
    cells = {("AR", slot): InputCell("AR", "AR_CLOSING_BALANCE", "202610", slot, D(500), ValueState.ENTERED,
                                     ValueOrigin.MANUAL)}
    r = calculate(TEMPLATE + [ar], CAL_202610, cells)
    assert r.metric_monthly[("AR_CLOSING_BALANCE", "202610")].amount == D(500)
    assert r.metric_monthly[("AR_CLOSING_BALANCE", "202611")].amount is None   # missing, not 0
    assert not any(k[0] == "AR" for k in r.line_weekly)                        # no weekly split


def test_editability_rules():
    first = "202610_W1"
    assert is_cell_editable(by_id("L_EMPLOYMENT"), "202611_W3", first)
    assert not is_cell_editable(by_id("L_TOTAL_CASH_INFLOW"), "202610_W1", first)
    assert not is_cell_editable(by_id("L_NET_CASH_MOVEMENT"), "202610_W1", first)
    assert not is_cell_editable(by_id("L_CLOSING_CASH_BALANCE"), "202610_W1", first)
    assert is_cell_editable(by_id("L_OPENING_CASH_BALANCE"), "202610_W1", first)
    assert not is_cell_editable(by_id("L_OPENING_CASH_BALANCE"), "202611_W1", first)


def test_fx_conversion_lcy_per_eur_and_missing_fx():
    assert to_eur(D(110), D("1.1")) == D(100)
    assert to_eur(D(110), None) is None
    assert to_eur(None, D("1.1")) is None
