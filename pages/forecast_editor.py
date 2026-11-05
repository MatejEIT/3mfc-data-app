"""Forecast Editor – the main working screen (weekly first, monthly derived)."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pandas as pd
import streamlit as st

from components import forecast_grid as G
from components import theme as T
from domain.metrics import IC_SPLIT_METRIC
from domain.models import IcDetail, Severity, SubmissionStatus, ValueOrigin
from services.auth_service import AccessDenied
from services.bootstrap import get_context, require_user
from services.repository import RevisionConflict

ctx = get_context()
user = require_user()

# ------------------------------------------------------------------ selection
entities = ctx.ref.entities()
allowed = entities if user.is_hq else entities[entities["CODE_BUSINESS_UNIT"].isin(user.business_units)]
if allowed.empty:
    st.warning("No entity is assigned to your account.")
    st.stop()
labels = {r.CODE_BUSINESS_UNIT: f"{r.CODE_BUSINESS_UNIT} · {r.NAME_ENTITY}" for r in allowed.itertuples()}
snapshots = ctx.repo.snapshots()

c1, c2, c3 = st.columns([3, 1.4, 1.6])
bu = c1.selectbox("Entity", list(labels), format_func=labels.get, key="sel_bu")
if not snapshots:
    st.info("No forecast snapshot exists yet. HQ creates the first snapshot in **HQ Workspace › Snapshots**.")
    st.stop()
snapshot = c2.selectbox("Snapshot", snapshots, key="sel_snapshot")
view = c3.segmented_control("View", ["Weekly", "Monthly"], default="Weekly", key="sel_view") or "Weekly"

ns = f"{snapshot}::{bu}"
ws_key, pend_key, ic_key, idem_key = f"ws::{ns}", f"pending::{ns}", f"ic::{ns}", f"idem::{ns}"


def load_workspace(force: bool = False):
    if force or ws_key not in st.session_state:
        with st.spinner("Loading forecast…"):
            st.session_state[ws_key] = ctx.forecasts.open(user, snapshot, bu)
    return st.session_state[ws_key]


try:
    ws = load_workspace()
except AccessDenied as exc:
    st.error(str(exc))
    st.stop()

if ws.state.head is None:
    st.info(f"No forecast for {labels[bu]} in snapshot {snapshot}.")
    st.stop()

pending: dict = st.session_state.setdefault(pend_key, {})
ic_rows: list[IcDetail] = st.session_state.setdefault(ic_key, list(ws.state.ic_details))
submitted = ws.status == SubmissionStatus.SUBMITTED
read_only = not user.can_edit(bu, submitted)

# ------------------------------------------------------------------ status bar
status_chip = T.chip("SUBMITTED", "ok") if submitted else T.chip("DRAFT", "warn")
fx_missing = [p for p, v in ws.fx_rates.items() if v is None]
chips = [
    T.chip(f"LCY {ws.currency}"),
    T.chip(f"FX {ws.fx_reference}", "bad" if fx_missing else ""),
    status_chip,
    T.chip(f"Revision {ws.state.revision_no} · {ws.state.head.kind.value}", "info"),
]
if pending:
    chips.append(T.chip(f"{len(pending)} unsaved change(s)", "warn"))
if read_only:
    chips.append(T.chip("read-only", ""))
st.markdown(" ".join(chips), unsafe_allow_html=True)

# ------------------------------------------------------------------ grid
T.legend()
new_pending, preview, parse_errors = G.render(ws, view, pending, read_only,
                                              key=f"grid::{ns}::{ws.state.revision_no}::{view}")
if new_pending != pending:
    st.session_state[pend_key] = new_pending
    st.rerun()

# ------------------------------------------------------------------ KPI strip
first_p, last_p = preview.periods[0], preview.periods[-1]
opening = preview.kpi("OPENING_CASH_BALANCE", first_p)
closing = preview.kpi("CLOSING_CASH_BALANCE", last_p)
net = sum((preview.kpi("NET_CASH_MOVEMENT", p) or Decimal(0)) for p in preview.periods)
weekly_closing = [preview.metric_weekly[("CLOSING_CASH_BALANCE", w.code_week_period)].amount for w in preview.weeks]
lowest = min((v for v in weekly_closing if v is not None), default=None)
k = st.columns(4)
k[0].markdown(T.kpi_card("Opening cash", T.fmt_money(opening), f"{ws.currency} · {G.period_label(first_p)}"),
              unsafe_allow_html=True)
k[1].markdown(T.kpi_card("Net cash movement", T.fmt_money(net), "3-month horizon",
                         "neg" if net < 0 else "pos"), unsafe_allow_html=True)
k[2].markdown(T.kpi_card("Closing cash", T.fmt_money(closing), f"{ws.currency} · {G.period_label(last_p)}"),
              unsafe_allow_html=True)
k[3].markdown(T.kpi_card("Lowest weekly cash", T.fmt_money(lowest), "across 12 intervals",
                         "neg" if lowest is not None and lowest < 0 else ""), unsafe_allow_html=True)

# ------------------------------------------------------------------ actions
cf_periods = sorted({c.code_period_value for c in ws.state.cells.values() if c.origin == ValueOrigin.CARRY_FORWARD})
reviewed = set(ws.state.head.carry_forward_reviewed)
if cf_periods and not read_only:
    for p in cf_periods:
        ok = st.checkbox(f"I have reviewed the carry-forward estimate for {G.period_label(p)}",
                         value=p in reviewed, key=f"cf::{ns}::{p}")
        reviewed = reviewed | {p} if ok else reviewed - {p}


def _save(submit: bool, comment: str) -> None:
    key = st.session_state.setdefault(idem_key, uuid.uuid4().hex)
    try:
        with st.spinner("Validating and saving…"):
            out = ctx.forecasts.save(user, ws, G.pending_to_edits(pending), ic_rows, comment, key,
                                     reviewed, submit=submit)
    except RevisionConflict as exc:
        st.session_state["flash"] = ("error", f"{exc} Your unsaved changes are kept – click **Reload latest**.")
        return
    except AccessDenied as exc:
        st.session_state["flash"] = ("error", str(exc))
        return
    st.session_state["last_issues"] = out.issues
    if out.revision is None:
        st.session_state["flash"] = ("warning", out.message)
        return
    for k_ in (pend_key, ic_key, idem_key):
        st.session_state.pop(k_, None)
    load_workspace(force=True)
    st.session_state["flash"] = ("success", out.message)


@st.dialog("Submit forecast")
def confirm_submit() -> None:
    st.write(f"Submit **{labels[bu]}** for snapshot **{snapshot}**? After submission the forecast is read-only "
             "for the entity; HQ can still adjust it (every change is audited).")
    comment = st.text_area("Comment (optional)", key="submit_comment")
    if st.button("Submit forecast", type="primary"):
        _save(True, comment)
        st.rerun()


if not read_only:
    a1, a2, a3, a4, a5 = st.columns([3.2, 1.1, 1.3, 1.1, 1.1])
    comment = a1.text_input("Change comment", placeholder="Reason for the change (stored in audit log)",
                            label_visibility="collapsed")
    if a2.button("Save draft" if not user.is_hq else "Save", type="secondary", disabled=bool(parse_errors),
                 use_container_width=True):
        _save(False, comment)
        st.rerun()
    if not submitted and a3.button("Submit forecast", type="primary", disabled=bool(parse_errors),
                                   use_container_width=True):
        confirm_submit()
    if a4.button("Discard", disabled=not pending, use_container_width=True):
        st.session_state.pop(pend_key, None)
        st.session_state.pop(ic_key, None)
        st.rerun()
    if a5.button("Reload latest", use_container_width=True):
        load_workspace(force=True)
        st.rerun()

flash = st.session_state.pop("flash", None)
if flash:
    getattr(st, flash[0])(flash[1])
if parse_errors:
    st.error("Some values are not valid numbers: " + "; ".join(sorted(set(parse_errors.values())))[:500])

# ------------------------------------------------------------------ details
tab_val, tab_ic, tab_tools, tab_hist = st.tabs(["Validations", "IC counterparties", "Bulk tools", "History"])

with tab_val:
    issues = st.session_state.get("last_issues") or ws.issues
    if not issues:
        st.success("No validation issues.")
    else:
        sev_order = {Severity.BLOCKING: 0, Severity.WARNING: 1, Severity.INFO: 2}
        df = pd.DataFrame([{"Severity": i.severity.value, "Code": i.code, "Message": i.message,
                            "Period": i.period, "Interval": i.week}
                           for i in sorted(issues, key=lambda i: (sev_order[i.severity], i.period, i.week))])
        n_block = (df["Severity"] == "BLOCKING").sum()
        st.caption(f"{n_block} blocking · {(df['Severity'] == 'WARNING').sum()} warnings. "
                   "Blocking issues prevent SUBMIT, not saving a draft.")
        st.dataframe(df, hide_index=True, use_container_width=True)

with tab_ic:
    st.caption("Split of **IC outflow – other VI group** (template row 89) by counterparty. The split must "
               "equal the main line in every interval before SUBMIT. IC elimination in consolidation is "
               "**not active** – business rules are not yet agreed.")
    ic_line = next((ln for ln in ws.lines if ln.metric_code == IC_SPLIT_METRIC), None)
    week_codes = [w.code_week_period for w in ws.calendar]
    cps = sorted(c for c in ws.valid_counterparties if c != bu)
    ic_df = pd.DataFrame([{"Interval": d.code_week_period, "Counterparty": d.counterparty, "Amount LCY": float(d.amount),
                           "Comment": d.comment, "_id": d.detail_id} for d in ic_rows],
                         columns=["Interval", "Counterparty", "Amount LCY", "Comment", "_id"])
    edited = st.data_editor(
        ic_df, num_rows="dynamic" if not read_only else "fixed", disabled=read_only, hide_index=True,
        use_container_width=True, key=f"ic_editor::{ns}::{ws.state.revision_no}",
        column_config={
            "Interval": st.column_config.SelectboxColumn(options=week_codes, required=True),
            "Counterparty": st.column_config.SelectboxColumn(options=cps, required=True),
            "Amount LCY": st.column_config.NumberColumn(format="%.2f", required=True),
            "_id": None,
        })
    new_ic = []
    for r in edited.to_dict("records"):
        amount = r.get("Amount LCY")
        if not r.get("Interval") or not r.get("Counterparty") or amount is None or pd.isna(amount):
            continue
        did = r.get("_id") if isinstance(r.get("_id"), str) and r.get("_id") else uuid.uuid4().hex
        new_ic.append(IcDetail(did, r["Interval"][:6], r["Interval"], r["Counterparty"],
                               Decimal(str(amount)).quantize(Decimal("0.01")), r.get("Comment") or "",
                               ValueOrigin.HQ_ADJUSTMENT if user.is_hq else ValueOrigin.MANUAL))
    if [(d.code_week_period, d.counterparty, d.amount, d.comment) for d in new_ic] != \
            [(d.code_week_period, d.counterparty, d.amount, d.comment) for d in ic_rows]:
        st.session_state[ic_key] = new_ic
        ic_rows = new_ic
    if ic_line:
        rec = []
        for w in week_codes:
            main = preview.line_weekly[(ic_line.line_id, w)].amount or Decimal(0)
            split = sum((d.amount for d in ic_rows if d.code_week_period == w), Decimal(0))
            if main or split:
                rec.append({"Interval": w, "Main line": float(main), "Allocated": float(split),
                            "Unallocated": float(main - split), "Status": "OK" if abs(main - split) <= Decimal("0.01")
                            else "UNALLOCATED"})
        if rec:
            st.dataframe(pd.DataFrame(rec), hide_index=True, use_container_width=True)

with tab_tools:
    if read_only:
        st.info("Forecast is read-only.")
    else:
        st.markdown("**Paste from Excel** – copy a block of numbers (rows × intervals) and choose where it starts.")
        input_lines = [ln for ln in ws.lines if ln.row_type.value == "DETAIL"]
        t1, t2 = st.columns(2)
        start_line = t1.selectbox("First line", [ln.line_id for ln in input_lines],
                                  format_func=lambda i: ws.lines_by_id[i].label, key=f"bp_line::{ns}")
        start_week = t2.selectbox("First interval", week_codes, key=f"bp_week::{ns}")
        block = st.text_area("Values (tab separated, one line item per row)", key=f"bp_text::{ns}", height=120)
        if st.button("Apply pasted values", disabled=not block.strip()):
            li = [ln.line_id for ln in input_lines].index(start_line)
            wi = week_codes.index(start_week)
            added = 0
            for r_off, text_row in enumerate(block.strip("\n").splitlines()):
                for c_off, raw in enumerate(text_row.split("\t")):
                    if li + r_off < len(input_lines) and wi + c_off < len(week_codes):
                        pending[(input_lines[li + r_off].line_id, week_codes[wi + c_off])] = raw.strip()
                        added += 1
            st.session_state[pend_key] = pending
            st.session_state["flash"] = ("info", f"{added} values staged – review the grid and save.")
            st.rerun()
        st.divider()
        st.markdown("**Copy an interval** – copies all input lines from one interval to another (staged, not saved).")
        s1, s2, s3 = st.columns([1, 1, 1])
        src = s1.selectbox("From", week_codes, key=f"cp_src::{ns}")
        dst = s2.selectbox("To", week_codes, index=min(1, len(week_codes) - 1), key=f"cp_dst::{ns}")
        if s3.button("Copy values", disabled=src == dst):
            for ln in input_lines:
                c = ws.state.cells.get((ln.line_id, src))
                stored = "" if c is None or c.amount is None else str(c.amount.quantize(Decimal("0.01")))
                pending[(ln.line_id, dst)] = pending.get((ln.line_id, src), stored)
            st.session_state[pend_key] = pending
            st.rerun()

with tab_hist:
    audit = ctx.repo.audit(snapshot, bu)
    if audit.empty:
        st.caption("No changes recorded yet.")
    else:
        names = {ln.line_id: ln.label for ln in ws.lines}
        view_df = audit.assign(Line=audit["LINE_ID"].map(names).fillna(audit["LINE_ID"].str[:10]))
        view_df = view_df.sort_values("CHANGED_AT_UTC", ascending=False)[
            ["CHANGED_AT_UTC", "CHANGED_BY", "CHANGED_ROLE", "CHANGE_TYPE", "REVISION_ID", "CODE_WEEK_PERIOD", "Line",
             "OLD_VALUE_LCY", "NEW_VALUE_LCY", "COMMENT"]]
        st.dataframe(view_df, hide_index=True, use_container_width=True, height=360)
