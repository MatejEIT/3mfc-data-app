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


def test_one_month_at_a_time_keeps_grid_narrow(snapshot_service, forecast_service, hq):
    ws = _ws(snapshot_service, forecast_service, hq)
    cells, errors = preview_cells(ws, {})
    result = calculate(ws.lines, ws.calendar, cells)
    _, fields = build_frame(ws, result, cells, "Weekly", {}, errors, read_only=False, periods=["202611"])
    assert fields == ["202611_W1", "202611_W2", "202611_W3", "202611_W4", "202611_M"]      # 5 columns, not 15
    _, fields = build_frame(ws, result, cells, "Monthly", {}, errors, read_only=False,
                            periods=["202610", "202611", "202612"])
    assert fields == ["202610_M", "202611_M", "202612_M"]


def test_cell_color_rules_use_ag_grid_expression_scope():
    from components.forecast_grid import _cell_rules
    rules = _cell_rules("202610_W1")
    assert all("data['_s_202610_W1']" in expr and "params" not in expr for expr in rules.values())


def test_add_ic_partner_row_offers_other_companies_and_creates_the_row(snapshot_service, forecast_service, hq):
    from components.forecast_grid import effective_lines
    from domain.ic_partners import partner_line_id, partner_marker
    ws = _ws(snapshot_service, forecast_service, hq)
    f, _ = _frame(ws)
    ids = list(f.index)
    assert "__icadd__::IN" in ids and "__icadd__::OUT" in ids
    opts_out = f.loc["__icadd__::OUT", "_opts"].split("|")
    assert any(o.startswith("V001DE · ") for o in opts_out)                # other companies offered
    assert not any(o.startswith(BU) for o in opts_out)                     # never the company itself
    assert not any(o.startswith("V016MX") for o in opts_out)               # preset partner already has a row
    # the add row sits right above the 'other / not specified' row
    assert ids[ids.index("__icadd__::OUT") + 1] == "L_INTERCOMPANY_OTHER_OUTFLOW"

    # picking V001DE (pending label) adds the partner row immediately
    new_id = partner_line_id(BU, "OUT", "V001DE")
    pending_labels = {new_id: partner_marker("OUT", "V001DE")}
    lines = effective_lines(ws, pending_labels)
    cells, errors = preview_cells(ws, {}, {ln.line_id: ln for ln in lines})
    result = calculate(lines, ws.calendar, cells)
    frame, _ = build_frame(ws, result, cells, "Weekly", {}, errors, read_only=False,
                           label_pending=pending_labels, lines=lines)
    frame = frame.set_index("_line_id")
    assert frame.loc[new_id, "Line"] == "IC to V001DE · VITRONIC Machine Vision GmbH"
    assert "V001DE" not in frame.loc["__icadd__::OUT", "_opts"]
    ro, _ = _frame(ws, read_only=True)
    assert "__icadd__::OUT" not in ro.index                                # no add row when read-only


def test_trash_icon_only_on_deletable_rows_and_staged_delete(snapshot_service, forecast_service, hq):
    from components.forecast_grid import _stage_delete
    from domain.ic_partners import partner_line_id
    ws = _ws(snapshot_service, forecast_service, hq)
    f, cells = _frame(ws)
    preset = partner_line_id(BU, "IN", "V016MX")
    assert f.loc["L_AR_NOT_OVERDUE_DETAIL", "_del"] == "1"        # customer row with values
    assert f.loc[preset, "_del"] == "1"                            # IC partner row
    assert f.loc["L_TAXES", "_del"] == ""                          # fixed template row
    assert f.loc["L_TOTAL_CASH_INFLOW", "_del"] == ""              # totals never

    pending, labels = {}, {}
    _stage_delete(ws, ws.lines_by_id, cells, "L_AR_NOT_OVERDUE_DETAIL", pending, labels)
    assert pending == {("L_AR_NOT_OVERDUE_DETAIL", "202610_W1"): ("", 1)}           # values cleared
    assert labels == {"L_AR_NOT_OVERDUE_DETAIL": "Project name and Customer name"}  # slot freed

    pending, labels = {(preset, "202610_W1"): ("5", 1000)}, {}
    _stage_delete(ws, ws.lines_by_id, cells, preset, pending, labels)
    assert pending == {} and labels == {}                          # preset partner: only its values go


def test_ar_ap_section_hidden_while_postponed(snapshot_service, forecast_service, hq):
    from domain.metrics import AR_AP_ENABLED
    assert AR_AP_ENABLED is False
    f, _ = _frame(_ws(snapshot_service, forecast_service, hq))
    assert "__section__BALANCES" not in f.index and not any("CLOSING_BALANCE" in str(i) and "CASH" not in str(i) for i in f.index)
