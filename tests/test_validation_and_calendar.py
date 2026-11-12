from datetime import date
from decimal import Decimal

import pytest

from domain.calendar import next_period, propose_month, validate_month
from domain.metrics import METRICS, check_dictionary_drift
from domain.validation import AmountParseError, Edit, parse_amount, validate_edits
from tests.conftest import BU, CAL_202610, TEMPLATE


@pytest.mark.parametrize("raw,expected", [
    ("1234.5", Decimal("1234.5")), ("1 234,50", Decimal("1234.50")), ("1.234,50", Decimal("1234.50")),
    ("1,234.50", Decimal("1234.50")), ("(500)", Decimal("-500")), ("-12", Decimal("-12")), ("0", Decimal(0)),
    ("1.234.567", Decimal("1234567")), (0, Decimal(0)), (12.5, Decimal("12.5")),
])
def test_parse_amount_accepts(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "  ", float("nan")])
def test_parse_amount_empty_is_none_not_zero(raw):
    assert parse_amount(raw) is None


@pytest.mark.parametrize("raw", ["abc", "NaN", "inf", "1,234", "1.234", "100.000", float("inf"), "1e20", True])
def test_parse_amount_rejects(raw):
    with pytest.raises(AmountParseError):
        parse_amount(raw)


def test_validate_edits_blocks_calculated_rows_and_precision():
    lines = {ln.line_id: ln for ln in TEMPLATE}
    ok, issues = validate_edits([
        Edit("L_TOTAL_CASH_INFLOW", "202610_W1", "5"),
        Edit("L_CLOSING_CASH_BALANCE", "202610_W2", "5"),
        Edit("L_EMPLOYMENT", "202610_W1", "1.0051"),
        Edit("L_EMPLOYMENT", "202610_W2", "abc"),
        Edit("L_EMPLOYMENT", "202701_W1", "1"),
        Edit("L_TAXES", "202610_W1", "100.00"),
        Edit("L_TAXES", "202610_W2", ""),
    ], lines, CAL_202610, BU)
    codes = sorted(i.code for i in issues)
    assert codes == ["E_NOT_EDITABLE", "E_NOT_EDITABLE", "E_NOT_NUMERIC", "E_PRECISION", "E_UNKNOWN_PERIOD"]
    assert [(e.line_id, v) for e, v in ok] == [("L_TAXES", Decimal("100.00")), ("L_TAXES", None)]


def test_validate_edits_rejects_duplicate_cell():
    lines = {ln.line_id: ln for ln in TEMPLATE}
    _, issues = validate_edits([Edit("L_TAXES", "202610_W1", "1"), Edit("L_TAXES", "202610_W1", "2")],
                               lines, CAL_202610, BU)
    assert [i.code for i in issues] == ["E_DUPLICATE_KEY"]


def test_calendar_proposal_matches_excel_pattern():
    for p in ("202610", "202611", "202612"):
        proposed = [(w.start, w.end) for w in propose_month(p)]
        excel = [(w.start, w.end) for w in CAL_202610 if w.code_period_value == p]
        assert proposed == excel
    jan = propose_month("202701")
    assert [(w.start, w.end) for w in jan] == [
        (date(2027, 1, 1), date(2027, 1, 10)), (date(2027, 1, 11), date(2027, 1, 17)),
        (date(2027, 1, 18), date(2027, 1, 24)), (date(2027, 1, 25), date(2027, 1, 31))]
    assert all(w.needs_confirmation for w in jan)
    assert validate_month(jan) == []


def test_next_period_crosses_year():
    assert next_period("202612") == "202701"
    assert next_period("202701", -1) == "202612"


def test_dictionary_drift_detection():
    rows = [{"METRIC_CODE": m.code, "PARENT_METRIC_CODE": m.parent or "", "ROW_TYPE": m.row_type.value,
             "TEMPLATE_ROW": f"{m.template_row}.00000"} for m in METRICS.values()]
    assert check_dictionary_drift(rows) == []
    rows[0]["PARENT_METRIC_CODE"] = "SOMETHING_ELSE"
    assert len(check_dictionary_drift(rows)) == 1


def test_parse_amount_accepts_numpy_numbers_returned_by_the_grid():
    import numpy as np
    assert parse_amount(np.float64(5.5)) == Decimal("5.5")
    assert parse_amount(np.int64(7)) == Decimal(7)
    assert parse_amount(np.float64("nan")) is None
