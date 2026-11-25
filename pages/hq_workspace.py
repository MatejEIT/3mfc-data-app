"""HQ Workspace – who has submitted, what HQ changed, and the monthly snapshot.

Layout: control panel (snapshot + headline numbers) · work area (status of all companies) ·
details (compare submitted vs current, create the next snapshot) collapsed.
"""
from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st

from components import report_ui as U
from components import theme as T
from components.forecast_grid import period_label
from domain.consolidation import IC_TOLERANCE_KEUR, ic_matrix, ic_reconciliation
from domain.ic_partners import single_partners
from domain.min_cash import classify, group_level
from domain.calendar import next_period, snapshot_periods
from services.auth_service import AccessDenied
from services.bootstrap import (cached_submissions_and_status, data_changed, get_context, remember_choice,
                                require_user, restore_choice)
from services.report_service import ReportService

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
        restore_choice("hq_snap", "app_snapshot", snapshots)
        snap = c1.selectbox("Snapshot", snapshots, key="hq_snap")
        remember_choice("app_snapshot", snap)
        months = [pd.Timestamp(f"{p[:4]}-{p[4:]}-01").strftime("%b %Y") for p in snapshot_periods(snap)]
        c1.caption(f"Forecast horizon: **{months[0]} – {months[-1]}**")
        subs, snap_status = cached_submissions_and_status(snap)     # one job, shared 60 s
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

        # ---- FINAL: HQ declares the current numbers of the snapshot final (read-only, used by HQ report)
        is_final = snap_status.get("STATUS") == "FINAL"

        @st.dialog("Mark snapshot as FINAL" if not is_final else "Reopen snapshot")
        def status_dialog() -> None:
            if is_final:
                st.write(f"Reopen **{snap}**? Companies and HQ can change the numbers again; the HQ report "
                         "will show the changed numbers until the snapshot is marked final again.")
            else:
                st.write(f"Mark **{snap}** as FINAL? The current version of every company becomes the final one: "
                         "the snapshot is read-only for everybody and the **HQ report** uses exactly these numbers. "
                         "It is recorded in the audit trail and can be reopened by HQ.")
            note = st.text_input("Comment", placeholder="e.g. October forecast final for the group report")
            if st.button("Reopen" if is_final else "Mark FINAL", type="primary", width="stretch"):
                with st.spinner("Saving…"):
                    (ctx.snapshots.reopen if is_final else ctx.snapshots.finalize)(user, snap, note)
                data_changed()
                for key_ in [k_ for k_ in st.session_state if str(k_).startswith(("report::", "ws::"))] +                         ["final_snapshots"]:
                    st.session_state.pop(key_, None)
                st.rerun()

        s1, s2 = st.columns([5, 1.2])
        if is_final:
            s1.markdown(T.chip("FINAL", "ok") + f' <span class="blk-sub">marked by '
                        f'{snap_status.get("CREATED_BY", "")} · {str(snap_status.get("CREATED_AT_UTC", ""))[:16]} UTC · '
                        'read-only, used by HQ report</span>', unsafe_allow_html=True)
        else:
            s1.markdown(T.chip("OPEN", "warn") + ' <span class="blk-sub">numbers can still change</span>',
                        unsafe_allow_html=True)
        if s2.button("Reopen" if is_final else "Mark FINAL", width="stretch", type="secondary" if is_final else "primary"):
            status_dialog()


# =========================================================================== 1a. HQ commentary
if view is not None:
    st.markdown(U.CSS, unsafe_allow_html=True)
    ckey = f"comments::{snap}"
    if ckey not in st.session_state:
        st.session_state[ckey] = ctx.repo.snapshot_comments(snap)
    versions = st.session_state[ckey]

    @st.dialog("HQ commentary", width="large")
    def comment_dialog() -> None:
        st.caption(f"Key points for snapshot {snap} – e.g. what is / is not included, assumptions for the approval. "
                   "Shown in HQ Workspace and HQ report; every save is kept as a new version.")
        text = st.text_area("Commentary", value=versions[0]["text"] if versions else "", height=260,
                            placeholder="• Shareholder loan: …\n• No additional Verazine payment included\n• …")
        if st.button("Save commentary", type="primary", width="stretch"):
            if not text.strip():
                st.warning("The commentary is empty.")
                return
            with st.spinner("Saving…"):
                ctx.snapshots.save_comment(user, snap, text)
            st.session_state.pop(ckey, None)
            st.rerun()

    with st.container(key="hq_comment"):
        h1, h2 = st.columns([5, 1.2], vertical_alignment="center")
        h1.markdown(f"**HQ commentary · snapshot {snap}**")
        if h2.button("Edit commentary" if versions else "Add commentary", width="stretch", icon=":material/edit_note:"):
            comment_dialog()
        if versions:
            st.markdown(U.commentary_card(versions[0]["text"], versions[0]["by"], versions[0]["at"]),
                        unsafe_allow_html=True)
            if len(versions) > 1:
                with st.expander(f"Earlier versions · {len(versions) - 1}"):
                    for v in versions[1:]:
                        st.caption(f"{v['by']} · {str(v['at'])[:16].replace('T', ' ')} UTC")
                        st.text(v["text"])
        else:
            st.caption("No commentary yet – add the key points for this snapshot (e.g. for the approval).")

# =========================================================================== 1b. cash overview
if view is not None:
    with st.container(key="cash_overview"):
        rkey = f"report::{snap}"
        if rkey not in st.session_state:
            with st.spinner(f"Consolidating snapshot {snap}…"):
                st.session_state[rkey] = ReportService(ctx.repo, ctx.ref).build(snap)
        rep = st.session_state[rkey]
        cons = rep.conso
        T.block_title("Cash overview",
                      f"Group in kEUR, intercompany eliminated · {len(rep.consolidated)} companies with values and FX "
                      f"{rep.fx_reference} · details in HQ report")
        if cons is None:
            st.info("Nothing to consolidate yet (no forecast values or FX rates).")
        else:
            st.markdown(U.CSS, unsafe_allow_html=True)
            wks = [w.code_week_period for w in cons.weeks]
            g_open, g_close = cons.monthly["opening"]["TOTAL"], cons.monthly["closing"]["TOTAL"]
            g_net = g_close - g_open
            series = [float(cons.weekly["closing"][w]) for w in wks]
            low_w = min(cons.weeks, key=lambda w: cons.weekly["closing"][w.code_week_period])
            low = cons.weekly["closing"][low_w.code_week_period]
            up = g_net >= 0
            g_lv = group_level([x.business_unit for x in rep.consolidated], rep.min_cash)
            g_state = classify(low, g_lv)
            g_txt = f" · group min {U.k(g_lv.minimum, False)}" if g_lv else ""
            st.markdown('<div class="rp-kpis" style="grid-template-columns:repeat(4,minmax(0,1fr))">' + "".join([
                U.kpi(f"Group opening · {period_label(cons.periods[0], True)}", U.k(g_open, False),
                      "Σ companies", "wallet", T.PRIMARY),
                U.kpi(f"Group closing · {period_label(cons.periods[-1], True)}", U.k(g_close, False),
                      "end of horizon", "flag", T.DARK_NAVY, extra=U.sparkline(series, 68, 26, color="#7CC0FF"),
                      dark=True),
                U.kpi("Change over 3 months", ("+" if up else "−") + U.k(abs(g_net), False),
                      f'<span class="rp-delta {"rp-up" if up else "rp-down"}">{"▲" if up else "▼"} '
                      f'{abs(g_net / g_open * 100):.0f} %</span> vs opening' if g_open else "vs opening",
                      "flow", T.POSITIVE if up else T.NEGATIVE),
                U.kpi("Lowest weekly cash", U.k(low, False),
                      f"{low_w.start:%d}–{low_w.end:%d %b}{g_txt}"
                      f"{' · BELOW' if g_state == 'below' else ''}", "low",
                      T.NEGATIVE if low < 0 or g_state == "below" else "#0EA5E9"),
            ]) + "</div>", unsafe_allow_html=True)

            head = ('<tr><th class="lbl">Company</th><th>Trend (weekly closing)</th>'
                    f'<th>Opening {period_label(cons.periods[0], True)}</th>'
                    f'<th>Closing {period_label(cons.periods[-1], True)}</th><th>Change</th><th>Lowest week</th>'
                    '<th>Min cash</th><th>vs minimum (−10 % / +20 %)</th></tr>')
            body = []
            rows = sorted(rep.consolidated, key=lambda x: -(x.flows.closing.get(wks[-1]) or 0))
            for x in rows:
                f = x.flows
                ser = [float(f.closing[w] or 0) for w in wks]
                o, cl = f.opening.get(wks[0]), f.closing.get(wks[-1])
                chg = (cl - o) if (o is not None and cl is not None) else None
                lowest = min((v for v in f.closing.values() if v is not None), default=None)
                arrow = "" if chg is None else (
                    f'<span style="color:{T.POSITIVE if chg >= 0 else T.NEGATIVE}">{"▲" if chg >= 0 else "▼"}</span> ')
                body.append(
                    f'<tr><td class="lbl"><b>{U.E(x.business_unit)}</b> <span style="color:{T.MUTED}">{U.E(x.name)}'
                    f'</span></td><td>{U.sparkline(ser, 120, 24, T.NEGATIVE if min(ser) < 0 else T.PRIMARY, "")}</td>'
                    f'{U.num_cell(o)}{U.num_cell(cl)}'
                    f'<td class="{"neg" if chg is not None and chg < 0 else ""}">{arrow}{U.k(chg) if chg is not None else "–"}</td>'
                    f'{U.num_cell(lowest)}{U.min_cash_cells(x.business_unit, lowest, rep.min_cash)}</tr>')
            with st.expander(f"Cash balance per company · kEUR · {len(rows)} companies", expanded=False):
                st.markdown(U.table([head], body), unsafe_allow_html=True)
                missing = [x.business_unit for x in rep.companies if x.expected and x not in rep.consolidated]
                if missing:
                    st.caption(f"Not included (no values or no FX rate): {', '.join(missing)}")
                if g_lv:
                    lo_, hi_ = g_lv.band
                    st.caption(f"Minimum cash per company (HQ treasury) · status from the lowest weekly cash: below = "
                               f"under minimum −{g_lv.lower * 100:.0f} %, above = over minimum +{g_lv.upper * 100:.0f} %. "
                               f"Group minimum = Σ company minimums {U.k(g_lv.minimum, False)} kEUR "
                               f"(band {U.k(lo_, False)}–{U.k(hi_, False)}).")

            # ---- intercompany overview (details: HQ report › Intercompany)
            recon = ic_reconciliation(cons)
            ic_tot = sum((r["diff"]["TOTAL"] for r in recon), Decimal(0))
            ic_limit = IC_TOLERANCE_KEUR * len(cons.periods)
            ic_ok = abs(ic_tot) <= ic_limit
            with st.expander(f"Intercompany · IC difference {U.k(ic_tot, False)} kEUR over 3 months · "
                             f"{'within tolerance' if ic_ok else 'to check'}", expanded=False):
                pill = lambda d: (f'<span class="rp-st" style="color:{"#047857" if abs(d) <= ic_limit else "#B91C1C"};'
                                  f'background:#F4F7FC">● {"OK" if abs(d) <= ic_limit else "Check"}</span>')
                body = [f'<tr><td class="lbl">{U.E(r["label"])}</td>{U.num_cell(r["received"]["TOTAL"])}'
                        f'{U.num_cell(r["paid"]["TOTAL"])}{U.num_cell(r["diff"]["TOTAL"])}<td>{pill(r["diff"]["TOTAL"])}'
                        '</td></tr>' for r in recon]
                body.append(f'<tr class="grand"><td class="lbl">IC difference</td><td></td><td></td>'
                            f'{U.num_cell(ic_tot)}<td></td></tr>')
                st.markdown(U.table(['<tr><th class="lbl">IC flow · 3 months</th><th>Received</th><th>Paid</th>'
                                     '<th>Difference</th><th>Status</th></tr>'], body), unsafe_allow_html=True)
                mx = ic_matrix(cons, [w.code_week_period for w in cons.weeks], pair_partner=single_partners(
                    dict(zip(entities["CODE_ENTITY"], entities["CODE_BUSINESS_UNIT"]))))
                bad = mx.mismatches(ic_limit)
                un_paid, un_rec = mx.unspecified
                st.caption(f"{len(bad)} named pair(s) do not match"
                           + (": " + " · ".join(f"**{m['payer']} → {m['receiver']}** {U.k(m['diff'], False)}"
                                                for m in bad[:4]) if bad else "")
                           + f" · without a named counterparty: {U.k(un_paid, False)} paid / {U.k(un_rec, False)} "
                             "received – payer × receiver matrix in **HQ report › Intercompany**.")

# =========================================================================== 2. work area
if view is not None:
    with st.container(key="work_area"):
        T.block_title(f"Companies · snapshot {snap}",
                      "Click a company to open it in Forecast Editor and adjust it – every HQ change is recorded.")
        table = view.sort_values(["Status", "CODE_BUSINESS_UNIT"])[
            ["CODE_BUSINESS_UNIT", "NAME_ENTITY", "CODE_REGION", "Status", "SUBMITTED_BY", "SUBMITTED_AT_UTC",
             "HQ adjusted", "UPDATED_AT_UTC"]].rename(columns={
                 "CODE_BUSINESS_UNIT": "Company", "NAME_ENTITY": "Name", "CODE_REGION": "Region",
                 "SUBMITTED_BY": "Submitted by", "SUBMITTED_AT_UTC": "Submitted at (UTC)",
                 "UPDATED_AT_UTC": "Last change (UTC)"}).reset_index(drop=True)
        event = st.dataframe(table, hide_index=True, width="stretch", key=f"ws_companies::{snap}",
                             on_select="rerun", selection_mode="single-row",
                             column_config={"HQ adjusted": st.column_config.CheckboxColumn()})
        picked = event.selection.rows if event else []
        if picked:
            row_ = table.iloc[picked[0]]
            if row_["Status"] == "No forecast":
                st.info(f"{row_['Company']} has no forecast in snapshot {snap} – nothing to open.")
            else:
                st.session_state["goto_forecast"] = (row_["Company"], snap)
                st.switch_page("pages/forecast_editor.py")

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

    if snap:
        with st.expander(f"Replace {snap} with Excel"):
            st.caption(f"The companies' Excel forecasts for **{snap}** (loaded in Keboola) replace the current "
                       "numbers. Each company gets a new version marked **Submitted** – earlier versions stay "
                       "in the history and audit. Same Excel twice = no change.")
            rbus = st.multiselect("Companies", list(entities["CODE_BUSINESS_UNIT"]),
                                  default=list(entities["CODE_BUSINESS_UNIT"]), key=f"repl_bus::{snap}")

            @st.dialog(f"Replace {snap} with Excel?")
            def replace_dialog() -> None:
                st.write(f"**{len(rbus)} companies** in snapshot **{snap}** get the numbers of their Excel file "
                         "and status **Submitted**. Changes made in the app since then stay only in the history.")
                if st.button("Replace", type="primary", width="stretch"):
                    bar = st.progress(0.0, text="Replacing…")
                    res = ctx.snapshots.replace_with_excel(
                        user, snap, rbus, on_progress=lambda i, n, b: bar.progress((i - 1) / n, text=f"{b} ({i}/{n})"))
                    bar.empty()
                    data_changed()
                    st.session_state["repl_result"] = (snap, res.created, res.skipped)
                    st.rerun()

            if st.button("Replace with Excel…", disabled=not rbus or is_final,
                         help="Snapshot is FINAL – reopen it first." if is_final else None):
                replace_dialog()
            done = st.session_state.get("repl_result")
            if done and done[0] == snap:
                st.success(f"Replaced with Excel: {len(done[1])} companies.")
                if done[2]:
                    st.caption("Skipped: " + "; ".join(f"{k}: {v}" for k, v in done[2].items()))
