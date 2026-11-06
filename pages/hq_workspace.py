"""HQ Workspace – submission overview, SUBMITTED vs CURRENT, snapshot lifecycle."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from domain.calendar import next_period
from services.auth_service import AccessDenied
from services.bootstrap import get_context, require_user

ctx = get_context()
user = require_user()
try:
    user.require_hq()
except AccessDenied as exc:
    st.error(str(exc))
    st.stop()

entities = ctx.ref.entities()
if "snapshots" not in st.session_state:
    st.session_state["snapshots"] = ctx.repo.snapshots()
snapshots = st.session_state["snapshots"]

tab_status, tab_compare, tab_snap = st.tabs(["Submission status", "Submitted vs current", "Snapshots"])

with tab_status:
    if not snapshots:
        st.info("No snapshot yet – create one in the **Snapshots** tab.")
    else:
        snap = st.selectbox("Snapshot", snapshots, key="hq_snap")
        subs = ctx.repo.submissions(snap)
        view = entities[["CODE_BUSINESS_UNIT", "NAME_ENTITY", "CODE_REGION", "CODE_LOCAL_CURRENCY"]].merge(
            subs, on="CODE_BUSINESS_UNIT", how="left").fillna("")
        view["Status"] = view["STATUS"].replace("", "NO FORECAST")
        view["Status"] = view.apply(lambda r: "NOT SUBMITTED" if r["Status"] == "DRAFT" else r["Status"], axis=1)
        view["HQ adjusted"] = view["HAS_HQ_ADJUSTMENTS"].eq("1")
        view["Notify entity"] = view["NOTIFICATION_PENDING"].eq("1")
        n_sub = (view["Status"] == "SUBMITTED").sum()
        m = st.columns(4)
        m[0].metric("Submitted", f"{n_sub} / {len(view)}")
        m[1].metric("Not submitted", int((view["Status"] == "NOT SUBMITTED").sum()))
        m[2].metric("No forecast", int((view["Status"] == "NO FORECAST").sum()))
        m[3].metric("Pending notifications", int(view["Notify entity"].sum()))
        st.dataframe(
            view[["CODE_BUSINESS_UNIT", "NAME_ENTITY", "CODE_REGION", "CODE_LOCAL_CURRENCY", "Status",
                  "SUBMITTED_BY", "SUBMITTED_AT_UTC", "CURRENT_REVISION_ID", "HQ adjusted", "Notify entity",
                  "UPDATED_AT_UTC"]].rename(columns={
                      "CODE_BUSINESS_UNIT": "Entity", "NAME_ENTITY": "Name", "CODE_REGION": "Region",
                      "CODE_LOCAL_CURRENCY": "LCY", "SUBMITTED_BY": "Submitted by", "SUBMITTED_AT_UTC": "Submitted at",
                      "CURRENT_REVISION_ID": "Current revision", "UPDATED_AT_UTC": "Last update"}),
            hide_index=True, width="stretch")
        st.caption("To edit an entity's forecast as HQ, open it in **Forecast Editor** – every saved change is "
                   "recorded as HQ_ADJUSTMENT in the audit log.")

with tab_compare:
    if snapshots:
        c1, c2 = st.columns(2)
        snap = c1.selectbox("Snapshot", snapshots, key="cmp_snap")
        subs = ctx.repo.submissions(snap)
        submitted = subs[subs["SUBMITTED_REVISION_ID"] != ""]
        if submitted.empty:
            st.info("No submitted forecast in this snapshot.")
        else:
            bu = c2.selectbox("Entity", list(submitted["CODE_BUSINESS_UNIT"]), key="cmp_bu")
            row = submitted[submitted["CODE_BUSINESS_UNIT"] == bu].iloc[0]
            cmp_key = f"cmp::{snap}::{bu}::{row['CURRENT_REVISION_ID']}"
            if cmp_key not in st.session_state and st.button("Compare submitted vs current", type="primary"):
                with st.spinner("Loading both revisions…"):
                    revs = ctx.repo.revisions(snap, bu)
                    sub_rev = next(r for r in revs if r.revision_id == row["SUBMITTED_REVISION_ID"])
                    ws = ctx.forecasts.open(user, snap, bu)
                    st.session_state[cmp_key] = (sub_rev, ws.state.revision_no, ctx.forecasts.diff_revisions(
                        ws, ctx.repo.load_state(snap, bu, sub_rev), ws.state))
            if cmp_key in st.session_state:
                sub_rev, current_no, diff = st.session_state[cmp_key]
                st.caption(f"Submitted revision **{sub_rev.revision_no}** by {sub_rev.created_by} · current revision "
                           f"**{current_no}**")
                if not diff:
                    st.success("No changes since submission.")
                else:
                    df = pd.DataFrame(diff)
                    for c in ("Submitted", "Current", "Difference"):
                        df[c] = df[c].map(lambda v: None if v is None else float(v))
                    st.dataframe(df, hide_index=True, width="stretch")

with tab_snap:
    st.markdown("**Create the next rolling snapshot (2+1).** Months 1–2 roll over from the previous snapshot's "
                "latest committed revision, month 3 is pre-filled as CARRY_FORWARD and must be reviewed by "
                "each entity. Existing forecasts are never overwritten.")
    latest = snapshots[0] if snapshots else None
    suggested = next_period(latest) if latest else ""
    c1, c2 = st.columns([1, 3])
    new_snap = c1.text_input("New snapshot (YYYYMM)", value=suggested)
    bus = c2.multiselect("Entities", list(entities["CODE_BUSINESS_UNIT"]), default=list(entities["CODE_BUSINESS_UNIT"]))
    cal = ctx.ref.calendar(new_snap) if len(new_snap) == 6 and new_snap.isdigit() else []
    if cal:
        cal_df = pd.DataFrame([{"Interval": w.code_week_period, "From": w.start, "To": w.end,
                                "Status": w.status.value} for w in cal])
        st.dataframe(cal_df, hide_index=True, width="stretch")
        if any(w.needs_confirmation for w in cal):
            st.warning("The calendar of at least one month is a PROPOSAL (PROPOSED_CONFIRM_REQUIRED). "
                       "Calendar confirmation by HQ is part of the next release; the snapshot can be created and the "
                       "affected intervals are flagged in validations.")
    b1, b2 = st.columns(2)
    if b1.button("Create rolling snapshot", type="primary", disabled=not (latest and new_snap and bus)):
        bar = st.progress(0.0, text="Creating snapshot…")
        res = ctx.snapshots.create_rollover(
            user, new_snap, bus, on_progress=lambda i, n, b: bar.progress((i - 1) / n, text=f"{b} ({i}/{n})"))
        bar.empty()
        st.session_state.pop("snapshots", None)
        st.success(f"Created for {len(res.created)} entities.")
        if res.skipped:
            st.warning("Skipped: " + "; ".join(f"{k}: {v}" for k, v in res.skipped.items()))
    if b2.button("Initial import from Excel seed", disabled=not (new_snap and bus),
                 help="One-time: creates revision 1 from FT_3MFC_APP_WEEKLY_INPUT_SEED for entities without a "
                      "forecast in this snapshot."):
        bar = st.progress(0.0, text="Importing…")
        res = ctx.snapshots.import_initial(
            user, new_snap, bus, on_progress=lambda i, n, b: bar.progress((i - 1) / n, text=f"{b} ({i}/{n})"))
        bar.empty()
        st.session_state.pop("snapshots", None)
        st.success(f"Imported {len(res.created)} entities.")
        if res.skipped:
            st.warning("Skipped: " + "; ".join(f"{k}: {v}" for k, v in res.skipped.items()))
