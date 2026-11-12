"""Forecast grid (AgGrid) styled after the entity Excel template "FC Vxxx".

What users know from Excel and find here again:
  * only light-yellow cells are inputs (#FFFFDE), everything else is calculated;
  * values in thousands of local currency ("in kUSD"), switchable to full units;
  * row texts, order and grouping of the template (CASH INFLOW / CASH OUTFLOW ...);
  * grey subtotals (#F2F2F2), darker totals (#D9D9D9), tinted month columns (#E7E9EC);
  * customer / project names are typed by the entity in the first column;
  * AR/AP balances are entered once per month (weekly cells greyed out).

Pending (unsaved) edits live in st.session_state and are previewed through the same
domain calculation engine the server uses on save, so totals update immediately.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import pandas as pd

from components import theme as T
from domain.calc import ForecastResult, calculate, is_cell_editable
from domain.ic_partners import (
    base_direction,
    parse_partner_marker,
    partner_direction,
    partner_line_id,
    partner_marker,
    with_added_partners,
)
from domain.metrics import AR_AP_ENABLED, METRICS, Aggregation, InputMode, Section, is_nameable, is_placeholder_label
from domain.models import InputCell, Provenance, RowType, ValueOrigin, ValueState, month_slot
from domain.validation import AmountParseError, Edit, parse_amount
from services.forecast_service import Workspace

MONTH_NAMES = {"01": "January", "02": "February", "03": "March", "04": "April", "05": "May", "06": "June",
               "07": "July", "08": "August", "09": "September", "10": "October", "11": "November", "12": "December"}

# (line_id, field) -> (raw text typed by the user, display scale at the time it was typed)
PendingEdits = dict[tuple[str, str], tuple[object, int]]
LabelEdits = dict[str, str]

SECTION_TITLES = {Section.INFLOW: "CASH INFLOW", Section.OUTFLOW: "CASH OUTFLOW",
                  Section.FINANCING: "FINANCING & CASH BALANCE", Section.BALANCES: "AR / AP BALANCES (month end)"}
PLACEHOLDER_HINT = "＋ type customer / project name"


def period_label(p: str, short: bool = False) -> str:
    name = MONTH_NAMES[p[4:]]
    return f"{name[:3]} {p[:4]}" if short else f"{name} {p[:4]}"


def unit_label(currency: str, scale: int) -> str:
    return f"in k{currency}" if scale == 1000 else f"in {currency}"


def to_storage(raw: object, scale: int) -> Decimal | None:
    """User input in the display unit (e.g. thousands) -> full local-currency units."""
    value = parse_amount(raw)
    return None if value is None else value * scale


def effective_lines(ws: Workspace, label_pending: LabelEdits | None = None) -> list:
    """Workspace lines + IC partner rows added in this session (not saved yet)."""
    return with_added_partners(ws.business_unit, ws.lines, {**ws.state.labels, **(label_pending or {})},
                               ws.company_names)


def preview_cells(ws: Workspace, pending: PendingEdits, lines_by_id: dict | None = None
                  ) -> tuple[dict, dict[tuple[str, str], str]]:
    """Apply pending edits for preview. Returns cells and per-cell parse errors."""
    cells = dict(ws.state.cells)
    errors: dict[tuple[str, str], str] = {}
    for (line_id, field), (raw, scale) in pending.items():
        line = (lines_by_id or ws.lines_by_id).get(line_id)
        if line is None:
            continue
        try:
            value = to_storage(raw, scale)
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


def _has_values(cells: dict, line_id: str, pending: PendingEdits) -> bool:
    if any(k[0] == line_id for k in pending):
        return True
    return any(c.state == ValueState.ENTERED for (lid, _), c in cells.items() if lid == line_id)


def _row_kind(ln) -> str:
    m = METRICS[ln.metric_code]
    if ln.metric_code == "NET_CASH_MOVEMENT":
        return "NCM"
    if m.aggregation == Aggregation.MONTH_STATE:
        return "ARAP"
    return ln.row_type.value


def _state_code(cells: dict, line, field: str, editable: bool, pending: PendingEdits, errors: dict) -> str:
    m = METRICS[line.metric_code]
    is_month = field.endswith("_M")
    if (line.line_id, field) in errors:
        return "ERR"
    if (line.line_id, field) in pending:
        return "PEND"
    if m.aggregation == Aggregation.MONTH_STATE and not is_month:
        return "NA"
    if not editable:
        return "CM" if is_month and line.row_type in (RowType.DETAIL, RowType.BALANCE) else "C"
    c = cells.get((line.line_id, field))
    if c is None or c.state == ValueState.MISSING:
        return "M" if m.required else "I"
    if c.origin == ValueOrigin.CARRY_FORWARD:
        return "CF"
    if c.origin == ValueOrigin.HQ_ADJUSTMENT:
        return "HQ"
    return "I"


def build_frame(ws: Workspace, result: ForecastResult, cells: dict, view: str, pending: PendingEdits,
                errors: dict, read_only: bool, show_empty: bool = False, scale: int = 1000,
                label_pending: LabelEdits | None = None, periods: list[str] | None = None,
                lines: list | None = None) -> tuple[pd.DataFrame, list[str]]:
    """`periods` limits the visible months (one month at a time keeps the grid narrow)."""
    label_pending = label_pending or {}
    lines = lines if lines is not None else ws.lines
    weeks_by_p: dict[str, list] = {}
    for w in ws.calendar:
        weeks_by_p.setdefault(w.code_period_value, []).append(w)
    fields: list[str] = []
    for p in result.periods:
        if periods and p not in periods:
            continue
        if view == "Weekly":
            fields += [w.code_week_period for w in weeks_by_p[p]]
        fields.append(month_slot(p))

    rows = []
    current_section = None
    free_slot_shown: set[str] = set()
    for ln in lines:
        m = METRICS[ln.metric_code]
        if m.section == Section.BALANCES and not AR_AP_ENABLED:
            continue                              # AR/AP postponed to the next phase
        nameable = is_nameable(ln.metric_code, ln.label)
        label = label_pending.get(ln.line_id, ws.label(ln))
        if parse_partner_marker(label):
            label = ln.label
        placeholder = nameable and is_placeholder_label(label)
        if nameable and placeholder and not _has_values(cells, ln.line_id, pending) and not show_empty:
            # Excel has ~15 free slots per list; show only the first free one ("add a customer").
            if read_only or ln.metric_code in free_slot_shown:
                continue
            free_slot_shown.add(ln.metric_code)

        if m.section != current_section:
            current_section = m.section
            rows.append({"_line_id": f"__section__{m.section.name}", "_kind": "SECTION",
                         "Line": SECTION_TITLES[m.section], "_editable": "", "_label_ed": "", "_ph": ""})
        direction = base_direction(ln)
        if direction and not read_only:
            rows.append(_ic_add_row(ws, lines, direction))        # "＋ add IC partner" above the 'other' row
        row = {"_line_id": ln.line_id, "_kind": _row_kind(ln), "Line": "" if placeholder else label,
               "_metric": m.label, "_label_ed": "1" if nameable and not read_only else "",
               "_ph": "1" if placeholder else "",
               "_del": "1" if not read_only and (ln.ic_partner or (nameable and (
                   not placeholder or _has_values(cells, ln.line_id, pending)))) else "",
               "_del_click": False}
        editable_fields = []
        for f in fields:
            is_month = f.endswith("_M")
            if m.aggregation == Aggregation.MONTH_STATE:
                val = cells.get((ln.line_id, f)) if is_month else None
                amount = val.amount if val is not None and val.state == ValueState.ENTERED else None
            elif is_month:
                mv = result.line_monthly[(ln.line_id, f[:6])]
                amount = mv.amount if (ln.row_type != RowType.DETAIL or mv.has_input) else None
            else:
                cv = result.line_weekly[(ln.line_id, f)]
                c = cells.get((ln.line_id, f))
                amount = cv.amount if ln.row_type != RowType.DETAIL or (c and c.state == ValueState.ENTERED) else None
            editable = (not read_only) and is_cell_editable(ln, f, ws.first_week) and (
                m.aggregation == Aggregation.MONTH_STATE or not is_month)
            if (ln.line_id, f) in errors:
                row[f] = str(pending[(ln.line_id, f)][0])
            else:
                row[f] = None if amount is None else float(amount / scale)
            row[f"_s_{f}"] = _state_code(cells, ln, f, editable, pending, errors)
            if editable:
                editable_fields.append(f)
        row["_editable"] = ",".join(editable_fields)
        rows.append(row)
    return pd.DataFrame(rows), fields


IC_ADD_HINT = "＋ add IC partner (choose company)"
# Must be a component *class* (init/getGui): st_aggrid runs AG Grid inside React, where a renderer
# function returning a DOM element crashes the grid (React error #31) and a returned string is plain text.
_DEL_RENDER = r"""class TrashRenderer {
  init(p) {
    this.eGui = document.createElement('span');
    if (!p.data || p.data._del !== '1') return;
    this.eGui.title = 'Delete row (applied when you Save)';
    this.eGui.setAttribute('role', 'button');
    this.eGui.style.cssText = 'display:inline-flex;align-items:center;justify-content:center;width:24px;' +
      'height:22px;border:1px solid #F5B5B5;border-radius:6px;background:#FFF5F5;cursor:pointer;margin-top:2px;';
    this.eGui.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#DC2626" ' +
      'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/>' +
      '<path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/></svg>';
    this.eGui.addEventListener('click', function (e) { e.stopPropagation(); p.node.setDataValue('_del_click', true); });
  }
  getGui() { return this.eGui; }
  refresh() { return false; }
}"""


def _clicked(value) -> bool:
    return value is True or str(value).strip().lower() in ("true", "1")


def _template_placeholder(metric_code: str) -> str:
    return "Name project expense" if metric_code == "PROJECT_EXPENSE_DETAIL" else "Project name and Customer name"


def _stage_delete(ws: Workspace, lines_by_id: dict, cells: dict, line_id: str,
                  pending: PendingEdits, label_pending: LabelEdits) -> None:
    """Trash icon: stage the deletion of a row as unsaved changes (undo with Discard).
    - IC partner added by the company: the row is removed
    - preset IC partner: its values are cleared (the pair itself is configuration)
    - customer / project row: values cleared and the slot returns to '＋ type customer / project name'"""
    line = lines_by_id.get(line_id)
    if line is None:
        return
    for key in [k for k in pending if k[0] == line_id]:
        pending.pop(key)
    if line.ic_partner:
        saved = parse_partner_marker(ws.state.labels.get(line_id, ""))
        if parse_partner_marker(label_pending.get(line_id, "")) and not saved:
            label_pending.pop(line_id)            # added in this session only -> just forget it
            return
        if saved:
            label_pending[line_id] = ""            # remove on Save
            return
    for (lid, field), c in cells.items():
        if lid == line_id and c.state == ValueState.ENTERED:
            pending[(lid, field)] = ("", 1)
    if is_nameable(line.metric_code, line.label):
        label_pending[line_id] = _template_placeholder(line.metric_code)


def _ic_add_row(ws: Workspace, lines: list, direction: str) -> dict:
    """Row with a company dropdown in the first column; picking a company adds a partner row."""
    taken = {ln.ic_partner for ln in lines if ln.ic_partner and partner_direction(ln) == direction}
    opts = [f"{bu} · {name}" for bu, name in sorted(ws.company_names.items())
            if bu != ws.business_unit and bu not in taken]
    return {"_line_id": f"__icadd__::{direction}", "_kind": "ICADD", "Line": "", "_label_ed": "1", "_ph": "1",
            "_hint": IC_ADD_HINT, "_opts": "|".join(opts), "_editable": "",
            "_metric": "Add a row for another group company you have intercompany payments with"}


# ----------------------------------------------------------------------------- AgGrid options
_FMT = """function(p){ if(p.value===null||p.value===undefined||p.value==='') return '';
  const n = Number(p.value); if (isNaN(n)) return String(p.value);
  const d = (p.context && p.context.decimals !== undefined) ? p.context.decimals : 0;
  const v = Math.abs(n) < Math.pow(10, -d) / 2 ? 0 : n;      // no "-0" for tiny negatives
  return v.toLocaleString('en-US',{minimumFractionDigits:d, maximumFractionDigits:d}); }"""
# Every callback guards `p.data`: AG Grid calls some of them without a row (e.g. the select editor
# formats its dropdown items through the column's valueFormatter with data = null).
_EDITABLE = """function(p){ if(!p.data) return false;
  return (',' + (p.data._editable||'') + ',').indexOf(',' + p.colDef.field + ',') >= 0; }"""
_LABEL_EDITABLE = "function(p){ return !!p.data && p.data._label_ed === '1'; }"
_LABEL_FMT = ("function(p){ if(!p.data) return p.value; "
              "if(p.data._ph==='1' && !p.value) return p.data._hint || '" + PLACEHOLDER_HINT + "'; return p.value; }")
_LABEL_EDITOR = """function(p){ if(p.data && p.data._kind==='ICADD'){ return {component:'agSelectCellEditor',
  params:{values: [''].concat((p.data._opts||'').split('|').filter(function(x){return x;}))}}; }
  return undefined; }"""
_ROW_CLASS = """function(p){ if(!p.data) return ''; const k=p.data._kind; return {SECTION:'r-section',
  TOTAL:'r-total', NCM:'r-ncm', SUBTOTAL:'r-subtotal', BALANCE:'r-balance', ARAP:'r-arap',
  DETAIL:'r-detail'}[k] || ''; }"""
_LABEL_CLASS = """function(p){ if(!p.data) return []; const c=['lbl-'+(p.data._kind||'')];
  if(p.data._label_ed==='1') c.push('lbl-input'); if(p.data._ph==='1') c.push('lbl-ph'); return c; }"""


def _cell_rules(field: str) -> dict:
    key = f"_s_{field}"
    # AG Grid evaluates string expressions with `data`, `x`, `node`... in scope (there is no `params`)
    rule = lambda code: f"(data['{key}']||'') === '{code}'"  # noqa: E731
    return {"c-calc": rule("C"), "c-calc-month": rule("CM"), "c-input": rule("I"), "c-missing": rule("M"),
            "c-cf": rule("CF"), "c-hq": rule("HQ"), "c-pend": rule("PEND"), "c-err": rule("ERR"), "c-na": rule("NA")}


def grid_css() -> dict:
    return {
        ".ag-root-wrapper": {"border": f"1px solid {T.BORDER} !important", "border-radius": "10px !important",
                             "font-family": "Arial, 'Source Sans Pro', sans-serif !important"},
        ".ag-header": {"background-color": f"{T.CARD} !important", "border-bottom": f"1px solid {T.BORDER} !important"},
        ".ag-header-group-cell": {"font-weight": "700", "color": f"{T.TEXT} !important"},
        ".h-week": {"background-color": f"{T.XL_WEEK_HEAD} !important"},
        ".h-month": {"background-color": f"{T.XL_MONTH} !important", "font-weight": "700"},
        ".ag-row": {"border-color": f"{T.XL_GRID} !important"},
        ".ag-cell": {"border-right": f"1px solid {T.XL_GRID} !important", "padding-left": "6px !important",
                     "padding-right": "6px !important"},
        ".ag-header-cell": {"padding-left": "6px !important", "padding-right": "6px !important"},
        # rows (Excel template hierarchy)
        ".r-section": {"background-color": f"{T.CARD} !important"},
        ".r-section .ag-cell": {"font-size": "14px", "font-weight": "700", "letter-spacing": ".03em",
                                "border-right": "none !important", "border-top": f"2px solid {T.TEXT} !important"},
        ".r-subtotal": {"background-color": f"{T.XL_SUBTOTAL} !important", "font-weight": "700"},
        ".r-total": {"background-color": f"{T.XL_TOTAL} !important", "font-weight": "700", "font-size": "13px"},
        ".r-ncm": {"background-color": f"{T.XL_TOTAL} !important", "font-weight": "700", "font-style": "italic",
                   "font-size": "13px"},
        ".r-balance": {"font-weight": "700"},
        ".r-detail .lbl-DETAIL": {"font-style": "italic", "padding-left": "26px !important"},
        ".lbl-ARAP": {"padding-left": "14px !important"},
        ".lbl-ICADD": {"padding-left": "26px !important", "font-style": "italic"},
        ".lbl-input": {"background-color": f"{T.XL_INPUT} !important", "cursor": "text"},
        ".lbl-ph": {"color": f"{T.MUTED} !important"},
        # cells
        ".c-input": {"background-color": f"{T.XL_INPUT} !important"},
        ".c-calc": {"color": "#1F2937"},
        ".c-calc-month": {"background-color": f"{T.XL_MONTH} !important", "font-weight": "600"},
        ".c-missing": {"background-color": f"{T.XL_INPUT} !important",
                       "box-shadow": f"inset 0 0 0 2px {T.NEGATIVE}"},
        ".c-cf": {"background-color": f"{T.CARRY_BG} !important", "box-shadow": f"inset 0 0 0 1px {T.WARNING}"},
        ".c-hq": {"background-color": f"{T.HQ_BG} !important"},
        ".c-pend": {"background-color": f"{T.PENDING_BG} !important", "font-weight": "700"},
        ".c-err": {"background-color": f"{T.MISSING_BG} !important", "color": f"{T.NEGATIVE} !important"},
        ".c-na": {"background-color": f"{T.XL_NA} !important"},
    }


def grid_options(ws: Workspace, frame: pd.DataFrame, fields: list[str], view: str, decimals: int):
    from st_aggrid import JsCode

    fmt, editable = JsCode(_FMT), JsCode(_EDITABLE)
    week_meta = {w.code_week_period: w for w in ws.calendar}

    def col(field: str) -> dict:
        is_month = field.endswith("_M")
        if is_month:
            header = "Total" if view == "Weekly" else MONTH_NAMES[field[4:6]]   # month name is in the group header
            tip = f"{period_label(field[:6])} – sum of the weeks (AR/AP: month-end balance input)"
        else:
            w = week_meta[field]
            header = f"{w.start.day}–{w.end.day}"
            tip = f"W{w.week_index}: {w.start:%d %b} – {w.end:%d %b %Y}" + (
                "  ·  calendar PROPOSED, to be confirmed by HQ" if w.needs_confirmation else "")
        return {"field": field, "headerName": header, "headerTooltip": tip, "type": "rightAligned",
                "flex": 1, "minWidth": 64 if not is_month else 76, "editable": editable, "valueFormatter": fmt,
                "cellClassRules": _cell_rules(field), "headerClass": "h-month" if is_month else "h-week",
                "singleClickEdit": True}

    groups, by_p = [], {}
    for f in fields:
        by_p.setdefault(f[:6], []).append(f)
    for p, fs in by_p.items():
        flag = " · proposed calendar" if any(week_meta.get(x) and week_meta[x].needs_confirmation for x in fs) else ""
        groups.append({"headerName": period_label(p) + flag, "children": [col(f) for f in fs],
                       "headerClass": "h-month" if view == "Monthly" else "h-week"})

    hidden = [{"field": c, "hide": True} for c in frame.columns if c.startswith("_") and c != "_del"]
    return {
        "columnDefs": [{"field": "Line", "headerName": unit_label(ws.currency, 1000 if decimals == 0 else 1),
                        "pinned": "left", "width": 270, "minWidth": 200, "tooltipField": "Line",
                        "editable": JsCode(_LABEL_EDITABLE), "valueFormatter": JsCode(_LABEL_FMT),
                        "cellEditorSelector": JsCode(_LABEL_EDITOR),
                        "cellClass": JsCode(_LABEL_CLASS), "singleClickEdit": True}] + groups
                      + [{"field": "_del", "colId": "del", "headerName": "", "pinned": "right", "width": 44, "maxWidth": 44,
                          "editable": False, "resizable": False, "cellRenderer": JsCode(_DEL_RENDER), "headerTooltip": "Delete row"}] + hidden,
        "defaultColDef": {"resizable": True, "sortable": False, "suppressMovable": True},
        "getRowClass": JsCode(_ROW_CLASS),
        "context": {"decimals": decimals},
        "rowHeight": 28, "headerHeight": 34, "groupHeaderHeight": 32,
        "enableCellTextSelection": True, "stopEditingWhenCellsLoseFocus": True,
        "undoRedoCellEditing": True, "tooltipShowDelay": 300,
        "getRowId": JsCode("function(p){ return p.data._line_id; }"),
    }


def render(ws: Workspace, view: str, pending: PendingEdits, read_only: bool, key: str, show_empty: bool = False,
           scale: int = 1000, label_pending: LabelEdits | None = None, periods: list[str] | None = None
           ) -> tuple[PendingEdits, LabelEdits, ForecastResult, dict]:
    """Draw the grid; return updated pending value edits, label edits, preview result and parse errors."""
    from st_aggrid import AgGrid

    label_pending = dict(label_pending or {})
    lines = effective_lines(ws, label_pending)
    cells, errors = preview_cells(ws, pending, {ln.line_id: ln for ln in lines})
    result = calculate(lines, ws.calendar, cells)
    frame, fields = build_frame(ws, result, cells, view, pending, errors, read_only, show_empty, scale, label_pending,
                                periods, lines)
    decimals = 0 if scale > 1 else 2
    resp = AgGrid(frame, gridOptions=grid_options(ws, frame, fields, view, decimals), allow_unsafe_jscode=True,
                  custom_css=grid_css(), height=min(40 + 28 * (len(frame) + 2), 780), theme="balham",
                  update_on=["cellValueChanged"], key=key, fit_columns_on_grid_load=False,
                  data_return_mode="AS_INPUT",
                  # unsaved edits live in session_state and the grid is rebuilt from them on every rerun,
                  # so the server must win (default 'client_wins' ignores new rows / resets after an edit)
                  server_sync_strategy="server_wins")
    new_pending = dict(pending)
    # st_aggrid returns its LAST response on every rerun. Only a new grid event may be applied,
    # otherwise the stale client state is diffed against the new server state again and again
    # (phantom edits + an endless rerun loop).
    if not _is_new_event(resp, key):
        return new_pending, label_pending, result, errors
    returned = resp.get("data") if isinstance(resp, dict) else getattr(resp, "data", None)
    if returned is not None and len(returned) == len(frame):
        returned = pd.DataFrame(returned).set_index("_line_id")
        original = frame.set_index("_line_id")
        for line_id, row in original.iterrows():
            if row.get("_kind") == "ICADD" and "Line" in returned.columns:
                choice = str(returned.at[line_id, "Line"] or "").strip()
                if choice and choice != IC_ADD_HINT:
                    direction, partner = str(line_id).split("::")[1], choice.split(" · ")[0].strip()
                    new_id = partner_line_id(ws.business_unit, direction, partner)
                    label_pending[new_id] = partner_marker(direction, partner)
                continue
            if row.get("_del") == "1" and "_del_click" in returned.columns                     and _clicked(returned.at[line_id, "_del_click"]):
                _stage_delete(ws, {ln.line_id: ln for ln in lines}, cells, str(line_id), new_pending, label_pending)
                continue
            if row.get("_label_ed") == "1" and "Line" in returned.columns:
                new_label = str(returned.at[line_id, "Line"] or "").strip()
                if new_label != str(row.get("Line") or "").strip() and new_label != PLACEHOLDER_HINT:
                    label_pending[line_id] = new_label
            allowed = set(filter(None, str(row.get("_editable", "")).split(",")))
            for f in allowed:
                before, after = row.get(f), returned.at[line_id, f] if f in returned.columns else row.get(f)
                if _same(before, after):
                    continue
                new_pending[(line_id, f)] = (after, scale)
    return new_pending, label_pending, result, errors


def _event_fingerprint(resp) -> str | None:
    """Stable identity of the grid event behind a response (None when no event happened yet)."""
    try:
        ev = resp.get("eventData") if isinstance(resp, dict) else getattr(resp, "event_data", None)
    except Exception:  # noqa: BLE001 - st_aggrid may raise before the first interaction
        return None
    if not isinstance(ev, dict) or not ev.get("type"):
        return None
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    col = ev.get("column") if isinstance(ev.get("column"), dict) else {}
    raw = json.dumps([ev.get("type"), ev.get("rowIndex"), col.get("colId"), data], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _is_new_event(resp, key: str) -> bool:
    import streamlit as st

    fp = _event_fingerprint(resp)
    if fp is None:
        return False
    state_key = f"__grid_last_event::{key}"
    if st.session_state.get(state_key) == fp:
        return False
    st.session_state[state_key] = fp
    return True


def _same(a, b) -> bool:
    def norm(x):
        if x is None or (isinstance(x, float) and pd.isna(x)) or (isinstance(x, str) and x.strip() == ""):
            return None
        try:
            return round(float(x), 9)
        except (TypeError, ValueError):
            return str(x)
    return norm(a) == norm(b)


def pending_to_edits(pending: PendingEdits) -> list[Edit]:
    """Convert display-unit input to full units; unparsable input is passed through for server-side rejection."""
    edits = []
    for (line_id, field), (raw, scale) in pending.items():
        try:
            value = to_storage(raw, scale)
            edits.append(Edit(line_id, field, "" if value is None else format(value.normalize(), "f")))
        except AmountParseError:
            edits.append(Edit(line_id, field, raw))
    return edits
