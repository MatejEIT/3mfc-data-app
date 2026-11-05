"""Hierarchical forecast grid (AgGrid) with per-cell editability and value-state styling.

Pending (unsaved) edits live in st.session_state and are previewed through the
same domain calculation engine that the server uses on save, so subtotals,
cash balances and monthly totals update immediately.
"""
from __future__ import annotations

import pandas as pd

from components import theme as T
from domain.calc import ForecastResult, calculate, is_cell_editable
from domain.metrics import METRICS, Aggregation, InputMode
from domain.models import InputCell, Provenance, RowType, ValueOrigin, ValueState, month_slot
from domain.validation import AmountParseError, Edit, parse_amount
from services.forecast_service import Workspace

MONTH_NAMES = {"01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May", "06": "Jun", "07": "Jul",
               "08": "Aug", "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec"}
PendingEdits = dict[tuple[str, str], object]   # (line_id, field) -> raw user value


def period_label(p: str) -> str:
    return f"{MONTH_NAMES[p[4:]]} {p[:4]}"


def preview_cells(ws: Workspace, pending: PendingEdits) -> tuple[dict, dict[tuple[str, str], str]]:
    """Apply pending edits for preview. Returns cells and per-cell parse errors."""
    cells = dict(ws.state.cells)
    errors: dict[tuple[str, str], str] = {}
    for (line_id, field), raw in pending.items():
        line = ws.lines_by_id.get(line_id)
        if line is None:
            continue
        try:
            value = parse_amount(raw)
        except AmountParseError as exc:
            errors[(line_id, field)] = str(exc)
            continue
        mode = METRICS[line.metric_code].input_mode
        state = ValueState.ENTERED if value is not None else (
            ValueState.BLANK if mode == InputMode.LINE_INPUT else ValueState.MISSING)
        old = cells.get((line_id, field))
        cells[(line_id, field)] = InputCell(line_id, line.metric_code, field[:6], field, value, state,
                                            ValueOrigin.MANUAL, old.provenance if old else Provenance())
    return cells, errors


def _state_code(ws: Workspace, cells: dict, line, field: str, editable: bool, pending: PendingEdits,
                errors: dict) -> str:
    if (line.line_id, field) in errors:
        return "ERR"
    if (line.line_id, field) in pending:
        return "PEND"
    if not editable:
        return "C"
    c = cells.get((line.line_id, field))
    if c is None or c.state == ValueState.MISSING:
        return "M" if METRICS[line.metric_code].required else "B"
    if c.origin == ValueOrigin.CARRY_FORWARD:
        return "CF"
    if c.origin == ValueOrigin.HQ_ADJUSTMENT:
        return "HQ"
    return "I" if c.state == ValueState.ENTERED else "B"


def _line_is_empty(ws: Workspace, cells: dict, line_id: str, pending: PendingEdits) -> bool:
    """A detail line without any entered value and without pending edits (unused template row)."""
    if any(k[0] == line_id for k in pending):
        return False
    return not any(c.state == ValueState.ENTERED for (lid, _), c in cells.items() if lid == line_id)


def build_frame(ws: Workspace, result: ForecastResult, cells: dict, view: str, pending: PendingEdits,
                errors: dict, read_only: bool, show_empty: bool = False) -> tuple[pd.DataFrame, list[str]]:
    weeks_by_p: dict[str, list] = {}
    for w in ws.calendar:
        weeks_by_p.setdefault(w.code_period_value, []).append(w)
    fields: list[str] = []
    for p in result.periods:
        if view == "Weekly":
            fields += [w.code_week_period for w in weeks_by_p[p]]
        fields.append(month_slot(p))

    rows = []
    current_section = None
    for ln in ws.lines:
        m = METRICS[ln.metric_code]
        if not show_empty and ln.row_type == RowType.DETAIL and m.input_mode == InputMode.LINE_INPUT \
                and _line_is_empty(ws, cells, ln.line_id, pending):
            continue
        if m.section != current_section:
            current_section = m.section
            rows.append({"_line_id": f"__section__{m.section.name}", "_kind": "SECTION", "Line": m.section.value,
                         "_editable": ""})
        indent = {RowType.DETAIL: "      ", RowType.SUBTOTAL: "   "}.get(ln.row_type, "")
        label = ln.label if ln.row_type == RowType.DETAIL else m.label
        row = {"_line_id": ln.line_id, "_kind": ln.row_type.value, "Line": indent + label,
               "_metric": m.label, "_row": ln.source_row}
        editable_fields = []
        for f in fields:
            is_month = f.endswith("_M")
            if m.aggregation == Aggregation.MONTH_STATE:
                val = cells.get((ln.line_id, f)) if is_month else None
                amount = val.amount if val is not None and val.state == ValueState.ENTERED else None
            elif is_month:
                mv = result.line_monthly[(ln.line_id, f[:6])]
                # a detail line with no input in the month stays empty (empty is not zero)
                amount = mv.amount if (ln.row_type != RowType.DETAIL or mv.has_input) else None
            else:
                cv = result.line_weekly[(ln.line_id, f)]
                c = cells.get((ln.line_id, f))
                amount = cv.amount if ln.row_type != RowType.DETAIL or (c and c.state == ValueState.ENTERED) else None
            editable = (not read_only) and is_cell_editable(ln, f, ws.first_week) and (
                m.aggregation == Aggregation.MONTH_STATE or not is_month)
            if (ln.line_id, f) in pending and (ln.line_id, f) in errors:
                row[f] = str(pending[(ln.line_id, f)])
            else:
                row[f] = None if amount is None else float(amount)
            row[f"_s_{f}"] = _state_code(ws, cells, ln, f, editable, pending, errors)
            if editable:
                editable_fields.append(f)
        row["_editable"] = ",".join(editable_fields)
        rows.append(row)
    return pd.DataFrame(rows), fields


_FMT = """function(p){ if(p.value===null||p.value===undefined||p.value==='') return '';
  const n = Number(p.value); if (isNaN(n)) return String(p.value);
  return n.toLocaleString('en-US',{minimumFractionDigits:0, maximumFractionDigits:2}); }"""
_EDITABLE = "function(p){ return (',' + (p.data._editable||'') + ',').indexOf(',' + p.colDef.field + ',') >= 0; }"
_ROW_CLASS = """function(p){ const k=p.data._kind; if(k==='SECTION') return 'r-section';
  if(k==='TOTAL') return 'r-total'; if(k==='SUBTOTAL') return 'r-subtotal';
  if(k==='BALANCE') return 'r-balance'; return ''; }"""


def _cell_rules(field: str) -> dict:
    key = f"_s_{field}"
    rule = lambda code: f"(params.data['{key}']||'') === '{code}'"  # noqa: E731
    return {"c-calc": rule("C"), "c-input": rule("I"), "c-blank": rule("B"), "c-missing": rule("M"),
            "c-cf": rule("CF"), "c-hq": rule("HQ"), "c-pend": rule("PEND"), "c-err": rule("ERR")}


_CSS = {
    ".ag-root-wrapper": {"border": f"1px solid {T.BORDER} !important", "border-radius": "10px !important"},
    ".ag-header": {"background-color": f"{T.BG} !important"},
    ".ag-header-group-cell-label": {"font-weight": "650", "color": T.DARK_NAVY},
    ".r-section": {"background-color": f"{T.DARK_NAVY} !important", "color": "#fff !important",
                   "font-weight": "650"},
    ".r-total": {"font-weight": "700", "border-top": f"1px solid {T.NAVY} !important"},
    ".r-subtotal": {"font-weight": "600"},
    ".r-balance": {"font-weight": "650", "color": T.DARK_NAVY},
    ".c-calc": {"background-color": f"{T.CALC_BG} !important", "color": "#334155"},
    ".c-input": {"background-color": f"{T.INPUT_BG} !important", "box-shadow": f"inset 0 0 0 1px {T.BORDER}"},
    ".c-blank": {"background-color": f"{T.INPUT_BG} !important", "box-shadow": f"inset 0 0 0 1px {T.BORDER}"},
    ".c-missing": {"background-color": f"{T.MISSING_BG} !important"},
    ".c-cf": {"background-color": f"{T.CARRY_BG} !important"},
    ".c-hq": {"background-color": f"{T.HQ_BG} !important"},
    ".c-pend": {"background-color": "#DBEAFE !important", "font-weight": "650"},
    ".c-err": {"background-color": f"{T.MISSING_BG} !important", "color": f"{T.NEGATIVE} !important"},
    ".month-col": {"font-weight": "650"},
}


def grid_options(ws: Workspace, frame: pd.DataFrame, fields: list[str], view: str):
    from st_aggrid import JsCode

    fmt, editable = JsCode(_FMT), JsCode(_EDITABLE)
    week_meta = {w.code_week_period: w for w in ws.calendar}

    def col(field: str) -> dict:
        is_month = field.endswith("_M")
        if is_month:
            header = "Month" if view == "Weekly" else period_label(field[:6])
            tip = f"{period_label(field[:6])} – derived from weeks (AR/AP: month-end balance input)"
        else:
            w = week_meta[field]
            header = f"W{w.week_index}  {w.start.day}–{w.end.day}"
            tip = f"{w.start:%d %b} – {w.end:%d %b %Y}" + ("  ·  calendar PROPOSED" if w.needs_confirmation else "")
        return {"field": field, "headerName": header, "headerTooltip": tip, "type": "rightAligned",
                "width": 118 if not is_month else 128, "editable": editable, "valueFormatter": fmt,
                "cellClassRules": _cell_rules(field), "cellClass": "month-col" if is_month else "",
                "singleClickEdit": True}

    groups, by_p = [], {}
    for f in fields:
        by_p.setdefault(f[:6], []).append(f)
    for p, fs in by_p.items():
        cal_flag = " · proposed calendar" if any(week_meta.get(x) and week_meta[x].needs_confirmation for x in fs) else ""
        groups.append({"headerName": period_label(p) + cal_flag, "children": [col(f) for f in fs]})

    hidden = [{"field": c, "hide": True} for c in frame.columns
              if c.startswith("_") and c not in ("_metric",)]
    return {
        "columnDefs": [{"field": "Line", "headerName": "Line item", "pinned": "left", "width": 330,
                        "tooltipField": "_metric", "cellStyle": {"white-space": "pre"}}] + groups + hidden
                      + [{"field": "_metric", "hide": True}],
        "defaultColDef": {"resizable": True, "sortable": False, "suppressMovable": True},
        "getRowClass": JsCode(_ROW_CLASS),
        "rowHeight": 30, "headerHeight": 34, "groupHeaderHeight": 32,
        "enableCellTextSelection": True, "stopEditingWhenCellsLoseFocus": True,
        "undoRedoCellEditing": True, "tooltipShowDelay": 300,
        "getRowId": JsCode("function(p){ return p.data._line_id; }"),
    }


def render(ws: Workspace, view: str, pending: PendingEdits, read_only: bool, key: str, show_empty: bool = False
           ) -> tuple[PendingEdits, ForecastResult, dict]:
    """Draw the grid; return updated pending edits, preview result and parse errors."""
    from st_aggrid import AgGrid

    cells, errors = preview_cells(ws, pending)
    result = calculate(ws.lines, ws.calendar, cells)
    frame, fields = build_frame(ws, result, cells, view, pending, errors, read_only, show_empty)
    resp = AgGrid(frame, gridOptions=grid_options(ws, frame, fields, view), allow_unsafe_jscode=True,
                  custom_css=_CSS, height=min(36 + 30 * (len(frame) + 2), 760), theme="balham",
                  update_on=["cellValueChanged"], key=key, fit_columns_on_grid_load=False,
                  data_return_mode="AS_INPUT")
    returned = resp.get("data") if isinstance(resp, dict) else getattr(resp, "data", None)
    new_pending = dict(pending)
    if returned is not None and len(returned) == len(frame):
        returned = pd.DataFrame(returned).set_index("_line_id")
        original = frame.set_index("_line_id")
        for line_id, row in original.iterrows():
            allowed = set(filter(None, str(row.get("_editable", "")).split(",")))
            for f in allowed:
                before, after = row.get(f), returned.at[line_id, f] if f in returned.columns else row.get(f)
                if _same(before, after):
                    continue
                new_pending[(line_id, f)] = after
    return new_pending, result, errors


def _same(a, b) -> bool:
    def norm(x):
        if x is None or (isinstance(x, float) and pd.isna(x)) or (isinstance(x, str) and x.strip() == ""):
            return None
        try:
            return float(x)
        except (TypeError, ValueError):
            return str(x)
    return norm(a) == norm(b)


def pending_to_edits(pending: PendingEdits) -> list[Edit]:
    return [Edit(line_id, field, raw) for (line_id, field), raw in pending.items()]
