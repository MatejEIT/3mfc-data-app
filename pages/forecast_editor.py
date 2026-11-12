"""Forecast Editor – the main working screen.

Layout (kept deliberately simple):
  1. control panel  – entity, snapshot, month, forecast status
  2. work area      – key figures, the grid (one month at a time), save / submit
  3. details        – validations, history (collapsed)
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pandas as pd
import streamlit as st

from components import forecast_grid as G
from components import theme as T
from domain.calendar import snapshot_periods
from domain.models import IcDetail, Severity, SubmissionStatus, ValueOrigin
from services.auth_service import AccessDenied
from services.bootstrap import get_context, require_user
from services.repository import RevisionConflict

ctx = get_context()
user = require_user()
SCALE = 1000                     # always thousands of local currency, like the Excel template

entities = ctx.ref.entities()
allowed = entities if user.is_hq else entities[entities["CODE_BUSINESS_UNIT"].isin(user.business_units)]
if allowed.empty:
    st.warning("No entity is assigned to your account.")
    st.stop()
labels = {r.CODE_BUSINESS_UNIT: f"{r.CODE_BUSINESS_UNIT} · {r.NAME_ENTITY}" for r in allowed.itertuples()}
if "snapshots" not in st.session_state:          # session-cached: cell edits must not hit Keboola
    st.session_state["snapshots"] = ctx.repo.snapshots()
snapshots = st.session_state["snapshots"]
if not snapshots:
    st.info("No forecast snapshot exists yet. HQ creates the first snapshot in **HQ Workspace**.")
    st.stop()

# =========================================================================== 1. control panel
panel = st.container(key="ctl_panel")
with panel:
    T.block_title("Select forecast", "Choose the company, the forecast snapshot and the month you want to work on.")
    c1, c2, c3 = st.columns([2.6, 1.1, 2.6])
    bu = c1.selectbox("Company", list(labels), format_func=labels.get, key="sel_bu")
    snapshot = c2.selectbox("Snapshot", snapshots, key="sel_snapshot")
    months = snapshot_periods(snapshot)
    ALL, TOTALS = "All weeks", "Month totals"
    period_opts = [ALL] + [G.period_label(p, short=True) for p in months] + [TOTALS]
    period_choice = c3.segmented_control("Show", period_opts, default=ALL, key=f"sel_period::{snapshot}",
                                         help="All weeks of the 3 months, a single month (bigger columns), or only "
                                              "the monthly totals.") or ALL
    status_slot = st.empty()

if period_choice == ALL:
    view, visible = "Weekly", months
elif period_choice == TOTALS:
    view, visible = "Monthly", months
else:
    view, visible = "Weekly", [months[period_opts.index(period_choice) - 1]]

ns = f"{snapshot}::{bu}"
ws_key, pend_key, ic_key, idem_key, lbl_key = (f"ws::{ns}", f"pending::{ns}", f"ic::{ns}", f"idem::{ns}",
                                                f"labels::{ns}")


def load_workspace(force: bool = False):
    if force:
        st.session_state.pop("snapshots", None)        # refresh the snapshot list with the forecast
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
label_pending: dict = st.session_state.setdefault(lbl_key, {})
ic_rows: list[IcDetail] = st.session_state.setdefault(ic_key, list(ws.state.ic_details))
submitted = ws.status == SubmissionStatus.SUBMITTED
read_only = not user.can_edit(bu, submitted)
unit = f"k{ws.currency}"
issues = st.session_state.get(f"issues::{ns}") or ws.issues
n_block = sum(i.severity == Severity.BLOCKING for i in issues)
n_warn = sum(i.severity == Severity.WARNING for i in issues)

chips = [T.chip("SUBMITTED", "ok") if submitted else T.chip("DRAFT – not submitted yet", "warn"),
         T.chip(f"Values in {unit}"),
         T.chip(f"FX {ws.fx_reference}", "bad" if any(v is None for v in ws.fx_rates.values()) else ""),
         T.chip(f"Revision {ws.state.revision_no}", "info")]
if n_block:
    chips.append(T.chip(f"{n_block} blocking issue(s) – see Validations below", "bad"))
if pending or label_pending:
    chips.append(T.chip(f"{len(pending) + len(label_pending)} unsaved change(s)", "warn"))
if read_only:
    chips.append(T.chip("read-only"))
status_slot.markdown(" ".join(chips), unsafe_allow_html=True)

# =========================================================================== 2. work area
work = st.container(key="work_area")
with work:
    title = labels[bu] + " · " + ("month totals" if view == "Monthly" else
                                  G.period_label(visible[0]) if len(visible) == 1 else "all weeks")
    T.block_title(title, "Edit the light-yellow cells; totals and cash balances update immediately. "
                         "Nothing is stored until you click Save." if not read_only else
                         "This forecast is read-only for you.")

    # key figures of the whole horizon (live preview incl. unsaved changes)
    kpi_slot = st.container()

    i1, i2 = st.columns([5.2, 1.3])
    with i1:
        T.instructions(ws.currency, SCALE)
    show_empty = i2.toggle("All template rows", value=False, key=f"show_empty::{ns}",
                           help="Only the first free customer / project row of each list is shown. "
                                "Switch on to see all ~15 rows of the Excel template.")
    new_pending, new_labels, preview, parse_errors = G.render(
        ws, view, pending, read_only,
        key=f"grid::{ns}::{ws.state.revision_no}::{view}::{'-'.join(visible)}::{show_empty}",
        show_empty=show_empty, scale=SCALE, label_pending=label_pending, periods=visible)
    if new_pending != pending or new_labels != label_pending:
        st.session_state[pend_key] = new_pending
        st.session_state[lbl_key] = new_labels
        st.rerun()
    T.legend()

    first_p, last_p = preview.periods[0], preview.periods[-1]
    opening = preview.kpi("OPENING_CASH_BALANCE", first_p)
    closing = preview.kpi("CLOSING_CASH_BALANCE", last_p)
    net = sum((preview.kpi("NET_CASH_MOVEMENT", p) or Decimal(0)) for p in preview.periods)
    lowest = min((v for v in (preview.metric_weekly[("CLOSING_CASH_BALANCE", w.code_week_period)].amount
                              for w in preview.weeks) if v is not None), default=None)

    def _k(v):
        return None if v is None else v / SCALE

    with kpi_slot:
        k = st.columns(4)
        k[0].markdown(T.kpi_card("Opening cash", T.fmt_money(_k(opening)), f"{unit} · {G.period_label(first_p)}"),
                      unsafe_allow_html=True)
        k[1].markdown(T.kpi_card("Net cash movement", T.fmt_money(_k(net)), f"{unit} · 3 months",
                                 "neg" if net < 0 else "pos"), unsafe_allow_html=True)
        k[2].markdown(T.kpi_card("Closing cash", T.fmt_money(_k(closing)), f"{unit} · {G.period_label(last_p)}"),
                      unsafe_allow_html=True)
        k[3].markdown(T.kpi_card("Lowest weekly cash", T.fmt_money(_k(lowest)), f"{unit} · 12 weeks",
                                 "neg" if lowest is not None and lowest < 0 else ""), unsafe_allow_html=True)
        st.write("")

    # carry-forward confirmation (only when the snapshot contains a pre-filled month)
    cf_periods = sorted({c.code_period_value for c in ws.state.cells.values()
                         if c.origin == ValueOrigin.CARRY_FORWARD})
    reviewed = set(ws.state.head.carry_forward_reviewed)
    if cf_periods and not read_only:
        for p in cf_periods:
            ok = st.checkbox(f"I have reviewed {G.period_label(p)} (pre-filled from the previous month)",
                             value=p in reviewed, key=f"cf::{ns}::{p}")
            reviewed = reviewed | {p} if ok else reviewed - {p}

    def _save(submit: bool, comment: str) -> None:
        key = st.session_state.setdefault(idem_key, uuid.uuid4().hex)
        try:
            with st.spinner("Validating and saving…"):
                out = ctx.forecasts.save(user, ws, G.pending_to_edits(pending), ic_rows, comment, key,
                                         reviewed, submit=submit, label_edits=label_pending)
        except RevisionConflict as exc:
            st.session_state["flash"] = ("error", f"{exc} Your unsaved changes are kept – click **Reload**.")
            return
        except AccessDenied as exc:
            st.session_state["flash"] = ("error", str(exc))
            return
        st.session_state[f"issues::{ns}"] = out.issues
        if out.revision is None:
            st.session_state["flash"] = ("warning", out.message)
            st.session_state[f"open_val::{ns}"] = True
            return
        for k_ in (pend_key, lbl_key, ic_key, idem_key):
            st.session_state.pop(k_, None)
        load_workspace(force=True)
        st.session_state["flash"] = ("success", out.message)

    @st.dialog("Submit forecast")
    def confirm_submit() -> None:
        st.write(f"Submit **{labels[bu]}** for snapshot **{snapshot}**? After submission the forecast is "
                 "read-only for you; HQ can still adjust it (every change is recorded).")
        comment = st.text_area("Comment (optional)", key="submit_comment")
        if st.button("Submit forecast", type="primary"):
            _save(True, comment)
            st.rerun()

    if not read_only:
        a1, a2, a3, a4, a5 = st.columns([3.0, 1.1, 1.4, 1.0, 1.0])
        comment = a1.text_input("Comment", placeholder="Optional comment for this change",
                                label_visibility="collapsed")
        if a2.button("Save" if user.is_hq else "Save draft", disabled=bool(parse_errors), width="stretch"):
            _save(False, comment)
            st.rerun()
        if not submitted and a3.button("Submit forecast", type="primary", disabled=bool(parse_errors),
                                       width="stretch"):
            confirm_submit()
        if a4.button("Discard", disabled=not (pending or label_pending), width="stretch",
                     help="Throw away all unsaved changes"):
            for k_ in (pend_key, lbl_key, ic_key):
                st.session_state.pop(k_, None)
            st.rerun()
        if a5.button("Reload", width="stretch", help="Load the latest saved version"):
            load_workspace(force=True)
            st.rerun()

    flash = st.session_state.pop("flash", None)
    if flash:
        getattr(st, flash[0])(flash[1])
    if parse_errors:
        st.error("Some values are not valid numbers: " + "; ".join(sorted(set(parse_errors.values())))[:500])

# =========================================================================== 3. details (collapsed)
details = st.container(key="details")
with details:
    val_title = "Validations · " + (f"{n_block} blocking, {n_warn} warnings" if issues else "all OK")
    with st.expander(val_title, expanded=bool(st.session_state.pop(f"open_val::{ns}", False))):
        st.caption("Blocking issues prevent Submit (a draft can always be saved). Warnings are for information.")
        if not issues:
            st.success("No validation issues.")
        else:
            order = {Severity.BLOCKING: 0, Severity.WARNING: 1, Severity.INFO: 2}
            st.dataframe(pd.DataFrame([{"Severity": i.severity.value, "Message": i.message, "Month": i.period,
                                        "Week": i.week}
                                       for i in sorted(issues, key=lambda i: (order[i.severity], i.period, i.week))]),
                         hide_index=True, width="stretch")

    week_codes = [w.code_week_period for w in ws.calendar]

    with st.expander("Change history"):
        st.caption("Every saved change: who, when, old and new value (in full local currency).")
        hist_key = f"audit::{ns}::{ws.state.revision_no}"
        if hist_key not in st.session_state:
            if st.button("Show history"):                 # loaded on demand, once per revision
                st.session_state[hist_key] = ctx.repo.audit(snapshot, bu)
                st.rerun()
        else:
            audit = st.session_state[hist_key]
            if audit.empty:
                st.caption("No changes recorded yet.")
            else:
                names = {ln.line_id: ws.label(ln) for ln in ws.lines}
                view_df = audit.assign(Row=audit["LINE_ID"].map(names).fillna(""))
                view_df = view_df.sort_values("CHANGED_AT_UTC", ascending=False)[
                    ["CHANGED_AT_UTC", "CHANGED_BY", "CHANGE_TYPE", "CODE_WEEK_PERIOD", "Row",
                     "OLD_VALUE_LCY", "NEW_VALUE_LCY", "COMMENT"]].rename(columns={
                        "CHANGED_AT_UTC": "When (UTC)", "CHANGED_BY": "Who", "CHANGE_TYPE": "Type",
                        "CODE_WEEK_PERIOD": "Week", "OLD_VALUE_LCY": "Old", "NEW_VALUE_LCY": "New",
                        "COMMENT": "Comment"})
                st.dataframe(view_df, hide_index=True, width="stretch", height=320)
