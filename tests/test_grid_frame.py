from components.forecast_grid import build_frame, preview_cells
from domain.calc import calculate
from tests.conftest import BU


def _ws(snapshot_service, forecast_service, hq):
    snapshot_service.import_initial(hq, "202610", [BU])
    return forecast_service.open(hq, "202610", BU)


def test_empty_template_lines_hidden_by_default_and_month_blank_not_zero(snapshot_service, forecast_service, hq):
    ws = _ws(snapshot_service, forecast_service, hq)
    cells, errors = preview_cells(ws, {})
    result = calculate(ws.lines, ws.calendar, cells)

    hidden, _ = build_frame(ws, result, cells, "Weekly", {}, errors, read_only=False)
    shown, _ = build_frame(ws, result, cells, "Weekly", {}, errors, read_only=False, show_empty=True)
    ids_hidden, ids_shown = set(hidden["_line_id"]), set(shown["_line_id"])

    assert "L_TAXES" not in ids_hidden and "L_TAXES" in ids_shown           # never filled -> hidden
    assert "L_AR_NOT_OVERDUE_DETAIL" in ids_hidden                          # has a value -> visible
    assert "L_EMPLOYMENT" in ids_hidden                                     # explicit 0 counts as a value
    assert "L_TOTAL_CASH_INFLOW" in ids_hidden                              # totals always visible

    row = shown.set_index("_line_id").loc["L_TAXES"]
    assert row["202610_M"] is None or row["202610_M"] != row["202610_M"]   # empty, not 0


def test_pending_edit_keeps_line_visible(snapshot_service, forecast_service, hq):
    ws = _ws(snapshot_service, forecast_service, hq)
    pending = {("L_TAXES", "202610_W1"): "5"}
    cells, errors = preview_cells(ws, pending)
    result = calculate(ws.lines, ws.calendar, cells)
    frame, _ = build_frame(ws, result, cells, "Weekly", pending, errors, read_only=False)
    assert "L_TAXES" in set(frame["_line_id"])
