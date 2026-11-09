"""HQ Workspace – who has submitted, what HQ changed, and the monthly snapshot.

Layout: control panel (snapshot + headline numbers) · work area (status of all companies) ·
details (compare submitted vs current, create the next snapshot) collapsed.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from components import theme as T
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

# =========================================================================== 1. control panel
snap, view = None, None
with st.container(key="ctl_panel"):
    T.block_title("Select snapshot", "One snapshot = one monthly 3-month forecast of all companies.")
    if not snapshots:
        st.info("No snapshot yet – create the first one under **New snapshot** below.")
    else:
        c1, c2 = st.columns([1.2, 5])
        snap = c1.selectbox("Snapshot", snapshots, key="hq_snap")
        subs = ctx.repo.submissions(snap)
        view = entities[["CODE_BUSINESS_UNIT", "NAME_ENTITY", "CODE_REGION", "CODE_LOCAL_CURRENCY"]].merge(
            subs, on="CODE_BUSINESS_UNIT", how="left").fillna("")
        view["Status"] = view["STATUS"].map({"SUBMITTED": "Submitted", "DRAFT": "Not submitted"}).fillna("No forecast")
        view.loc[view["STATUS"] == "", "Status"] = "No forecast"
        view["HQ adjusted"] = view["HAS_HQ_ADJUSTMENTS"].eq("1")
        with c2:
            k = st.columns(4)
            n = len(view)
            k[0].markdown(T.kpi_card("Submitted", f"{(view['Status'] == 'Submitted').sum()} / {n}", "companies",
                                     "pos"), unsafe_allow_html=True)
            k[1].markdown(T.kpi_card("Not submitted", str((view["Status"] == "Not submitted").sum()), "draft in progress",
                                     "warn"), unsafe_allow_html=True)
            k[2].markdown(T.kpi_card("No forecast", str((view["Status"] == "No forecast").sum()), "no data in snapshot"),
                          unsafe_allow_html=True)
            k[3].markdown(T.kpi_card("Adjusted by HQ", str(int(view["HQ adjusted"].sum())), "after submission"),
                          unsafe_allow_html=True)

# =========================================================================== 2. work area
if view is not None:
    with st.container(key="work_area"):
        T.block_title(f"Companies · snapshot {snap}",
                      "To change a company's numbers, open it in Forecast Editor – every HQ change is recorded.")
        table = view.sort_values(["Status", "CODE_BUSINESS_UNIT"])[
            ["CODE_BUSINESS_UNIT", "NAME_ENTITY", "CODE_REGION", "Status", "SUBMITTED_BY", "SUBMITTED_AT_UTC",
             "HQ adjusted", "UPDATED_AT_UTC"]].rename(columns={
                 "CODE_BUSINESS_UNIT": "Company", "NAME_ENTITY": "Name", "CODE_REGION": "Region",
                 "SUBMITTED_BY": "Submitted by", "SUBMITTED_AT_UTC": "Submitted at (UTC)",
                 "UPDATED_AT_UTC": "Last change (UTC)"})
        st.dataframe(table, hide_index=True, width="stretch",
                     column_config={"HQ adjusted": st.column_config.CheckboxColumn()})

# =========================================================================== 3. details (collapsed)
with st.container(key="details"):
    if view is not None:
        with st.expander("Compare submitted vs current"):
            st.caption("Shows every cell HQ (or anyone) changed after the company submitted.")
            submitted = view[view["SUBMITTED_REVISION_ID"] != ""]
            if submitted.empty:
                st.info("No company has submitted in this snapshot yet.")
            else:
                bu = st.selectbox("Company", list(submitted["CODE_BUSINESS_UNIT"]), key="cmp_bu")
                row = submitted[submitted["CODE_BUSINESS_UNIT"] == bu].iloc[0]
                cmp_key = f"cmp::{snap}::{bu}::{row['CURRENT_REVISION_ID']}"
                if cmp_key not in st.session_state and st.button("Compare", type="primary"):
                    with st.spinner("Loading both versions…"):
                        revs = ctx.repo.revisions(snap, bu)
                        sub_rev = next(r for r in revs if r.revision_id == row["SUBMITTED_REVISION_ID"])
                        ws = ctx.forecasts.open(user, snap, bu)
                        st.session_state[cmp_key] = (sub_rev, ws.state.revision_no, ctx.forecasts.diff_revisions(
                            ws, ctx.repo.load_state(snap, bu, sub_rev), ws.state))
                if cmp_key in st.session_state:
                    sub_rev, current_no, diff = st.session_state[cmp_key]
                    st.caption(f"Submitted version {sub_rev.revision_no} by {sub_rev.created_by} · "
                               f"current version {current_no}")
                    if not diff:
                        st.success("No changes since submission.")
                    else:
                        df = pd.DataFrame(diff)
                        for c in ("Submitted", "Current", "Difference"):
                            df[c] = df[c].map(lambda v: None if v is None else float(v))
                        st.dataframe(df, hide_index=True, width="stretch")

    with st.expander("New snapshot", expanded=not snapshots):
        st.caption("Rolling 2+1: months 1–2 are taken over from the previous snapshot, month 3 is pre-filled from "
                   "month 2 and each company confirms it. Existing forecasts are never overwritten.")
        latest = snapshots[0] if snapshots else None
        c1, c2 = st.columns([1, 3])
        new_snap = c1.text_input("New snapshot (YYYYMM)", value=next_period(latest) if latest else "")
        all_bus = list(entities["CODE_BUSINESS_UNIT"])
        bus = c2.multiselect("Companies", all_bus, default=all_bus)
        cal = ctx.ref.calendar(new_snap) if len(new_snap) == 6 and new_snap.isdigit() else []
        if cal:
            with st.popover("Week calendar of this snapshot"):
                st.dataframe(pd.DataFrame([{"Week": w.code_week_period, "From": w.start, "To": w.end,
                                            "Status": w.status.value} for w in cal]), hide_index=True)
            if any(w.needs_confirmation for w in cal):
                st.caption("⚠ At least one month uses a proposed week calendar – it is flagged in validations.")
        b1, b2 = st.columns(2)

        def _run(fn, label: str) -> None:
            bar = st.progress(0.0, text=label)
            res = fn(user, new_snap, bus, on_progress=lambda i, n, b: bar.progress((i - 1) / n, text=f"{b} ({i}/{n})"))
            bar.empty()
            st.session_state.pop("snapshots", None)
            st.success(f"Done for {len(res.created)} companies.")
            if res.skipped:
                st.caption("Skipped: " + "; ".join(f"{k}: {v}" for k, v in res.skipped.items()))

        if b1.button("Create rolling snapshot", type="primary", disabled=not (latest and new_snap and bus),
                     width="stretch"):
            _run(ctx.snapshots.create_rollover, "Creating snapshot…")
        if b2.button("Initial import from Excel", disabled=not (new_snap and bus), width="stretch",
                     help="One-time: creates the first version from the imported Excel files for companies "
                          "without a forecast in this snapshot."):
            _run(ctx.snapshots.import_initial, "Importing…")
