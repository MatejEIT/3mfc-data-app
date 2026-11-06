from decimal import Decimal

from components.forecast_grid import build_frame, pending_to_edits, preview_cells
from domain.calc import calculate
from tests.conftest import BU


def _ws(snapshot_service, forecast_service, hq):
    snapshot_service.import_initial(hq, "202610", [BU])
    return forecast_service.open(hq, "202610", BU)


def _frame(ws, pending=None, **kw):
    pending = pending or {}
    cells, errors = preview_cells(ws, pending)
    result = calculate(ws.lines, ws.calendar, cells)
    frame, _ = build_frame(ws, result, cells, "Weekly", pending, errors, **{"read_only": False, **kw})
    return frame.set_index("_line_id"), cells


def test_layout_follows_excel_template(snapshot_service, forecast_service, hq):
    ws = _ws(snapshot_service, forecast_service, hq)
    f, _ = _frame(ws)
    ids = list(f.index)
    assert "L_TAXES" in ids                                   # fixed template rows always visible (as in Excel)
    assert "L_AR_PH1" in ids and "L_AR_PH2" not in ids         # only the first free customer slot is offered
    assert f.loc["L_AR_PH1", "_ph"] == "1" and f.loc["L_AR_PH1", "Line"] == ""
    assert f.loc["L_AR_PH1", "_label_ed"] == "1"               # customer name typed in the first column
    assert f.loc["L_TAXES", "_label_ed"] == ""                 # fixed rows cannot be renamed
    assert f.loc["L_TAXES", "_s_202610_W1"] == "I"             # yellow input
    assert f.loc["L_TOTAL_CASH_INFLOW", "_s_202610_W1"] == "C"  # calculated
    assert f.loc["L_EMPLOYMENT", "_s_202610_M"] == "CM"        # tinted month column
    assert ids.index("__section__INFLOW") < ids.index("L_AR_NOT_OVERDUE_COLLECTIONS")
    assert f.loc["__section__INFLOW", "Line"] == "CASH INFLOW"

    f_all, _ = _frame(ws, show_empty=True)
    assert "L_AR_PH2" in f_all.index
    f_ro, _ = _frame(ws, read_only=True)
    assert "L_AR_PH1" not in f_ro.index                        # no free slots in read-only mode


def test_values_shown_and_entered_in_thousands(snapshot_service, forecast_service, hq):
    ws = _ws(snapshot_service, forecast_service, hq)
    f, _ = _frame(ws, scale=1000)
    assert f.loc["L_AR_NOT_OVERDUE_DETAIL", "202610_W1"] == 200.0   # 200 000 LCY shown as 200 k
    f1, _ = _frame(ws, scale=1)
    assert f1.loc["L_AR_NOT_OVERDUE_DETAIL", "202610_W1"] == 200000.0

    pending = {("L_TAXES", "202610_W1"): ("5,5", 1000)}
    _, cells = _frame(ws, pending)
    assert cells[("L_TAXES", "202610_W1")].amount == Decimal("5500")
    assert [(e.line_id, e.raw_value) for e in pending_to_edits(pending)] == [("L_TAXES", "5500")]


def test_month_of_unused_line_is_empty_not_zero(snapshot_service, forecast_service, hq):
    ws = _ws(snapshot_service, forecast_service, hq)
    f, _ = _frame(ws)
    v = f.loc["L_TAXES", "202610_M"]
    assert v is None or v != v                                 # None / NaN, never 0
