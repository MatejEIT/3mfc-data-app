"""HQ report – consolidated 3-month cash-flow forecast of the group in kEUR (replica of Excel "FC CONSO").

Layout: control panel (snapshot, view, key figures) · work area (charts, FC CONSO table, Excel export) ·
details (cash per company, completeness & FX, checks, company detail, assumptions) collapsed.
"""
from __future__ import annotations

import html
import io
from decimal import Decimal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from components import theme as T
from components.forecast_grid import period_label
from domain.consolidation import CONSO_ROWS, IC_TOLERANCE_KEUR, bridge, consolidate
from services.auth_service import AccessDenied
from services.bootstrap import get_context, require_user
from services.report_service import CHECK, CRITICAL, INFO, Report, ReportService

ctx = get_context()
user = require_user()
try:
    user.require_hq()
except AccessDenied as exc:
    st.error(str(exc))
    st.stop()


def k(v) -> str:
    """Whole kEUR, '–' for ~0, true minus sign."""
    if v is None:
        return ""
    if abs(v) < Decimal("0.5"):
        return "–"
    s = f"{abs(v):,.0f}".replace(",", " ")
    return f"−{s}" if v < 0 else s


# =========================================================================== data
if "snapshots" not in st.session_state:
    st.session_state["snapshots"] = ctx.repo.snapshots()
snapshots = st.session_state["snapshots"]
if not snapshots:
    st.info("No snapshot yet.")
    st.stop()
if "final_snapshots" not in st.session_state:
    st.session_state["final_snapshots"] = ctx.repo.final_snapshots()
finals = st.session_state["final_snapshots"]
default = finals[0] if finals else snapshots[0]


def load_report(snap: str, force: bool = False) -> Report:
    key = f"report::{snap}"
    if force or key not in st.session_state:
        with st.spinner(f"Consolidating snapshot {snap}…"):
            st.session_state[key] = ReportService(ctx.repo, ctx.ref).build(snap)
    return st.session_state[key]


# =========================================================================== 1. control panel
with st.container(key="ctl_panel"):
    T.block_title("HQ report", "Consolidated cash-flow forecast of the group in kEUR – structure and logic of the "
                               "Excel sheet “FC CONSO”; intercompany payments are eliminated.")
    c1, c2, c3, c4 = st.columns([1.1, 1.3, 3.2, 0.9])
    snap = c1.selectbox("Snapshot", snapshots, index=snapshots.index(default), key="rep_snap",
                        format_func=lambda s: f"{s} · FINAL" if s in finals else s)
    view = c2.segmented_control("Show", ["Weeks", "Months"], default="Weeks", key="rep_view") or "Weeks"
    if c4.button("Refresh", width="stretch", help="Reload the numbers from Keboola"):
        st.session_state.pop("final_snapshots", None)
        load_report(snap, force=True)
        st.rerun()
    rep = load_report(snap)
    with c3:
        if rep.status == "FINAL":
            st.markdown(T.chip("FINAL", "ok") + f' <span class="blk-sub">marked by {html.escape(rep.status_by)} · '
                        f'{html.escape(rep.status_at[:16].replace("T", " "))} UTC · FX {html.escape(rep.fx_reference)}'
                        '</span>', unsafe_allow_html=True)
        else:
            st.markdown(T.chip("NOT FINAL – numbers can still change", "warn")
                        + ' <span class="blk-sub">mark it final in HQ Workspace</span>', unsafe_allow_html=True)

    c = rep.conso
    n_all, n_ok = len([x for x in rep.companies if x.status != "No forecast"]), len(rep.consolidated)
    n_crit = sum(ch["Severity"] == CRITICAL for ch in rep.checks)
    n_chk = sum(ch["Severity"] == CHECK for ch in rep.checks)
    if c is not None:
        wk = [w.code_week_period for w in c.weeks]
        low_w = min(c.weeks, key=lambda w: c.weekly["closing"][w.code_week_period])
        low = c.weekly["closing"][low_w.code_week_period]
        ic_total = c.monthly["ic_diff"]["TOTAL"]
        kp = st.columns(6)
        kp[0].markdown(T.kpi_card(f"Opening · {period_label(c.periods[0], True)}", k(c.monthly["opening"]["TOTAL"]),
                                  "kEUR · Σ companies"), unsafe_allow_html=True)
        net = c.monthly["net"]["TOTAL"]
        kp[1].markdown(T.kpi_card("Net cash movement", k(net), "kEUR · 3 months, excl. IC", "pos" if net >= 0 else "neg"),
                       unsafe_allow_html=True)
        kp[2].markdown(T.kpi_card(f"Closing · {period_label(c.periods[-1], True)}", k(c.monthly["closing"]["TOTAL"]),
                                  "kEUR · group"), unsafe_allow_html=True)
        kp[3].markdown(T.kpi_card("Lowest weekly cash", k(low),
                                  f"kEUR · {low_w.start:%d}–{low_w.end:%d %b}", "neg" if low < 0 else ""),
                       unsafe_allow_html=True)
        kp[4].markdown(T.kpi_card("IC difference", k(ic_total), f"kEUR · tolerance ±{IC_TOLERANCE_KEUR:.0f}/month",
                                  "warn" if abs(ic_total) > IC_TOLERANCE_KEUR else ""), unsafe_allow_html=True)
        kp[5].markdown(T.kpi_card("Companies", f"{n_ok} / {n_all}", f"{n_crit} critical · {n_chk} to check",
                                  "neg" if n_crit else ("warn" if n_chk else "pos")), unsafe_allow_html=True)

if rep.conso is None:
    st.warning("No company could be consolidated in this snapshot (no forecasts or no FX rates).")
    st.stop()
c = rep.conso

# =========================================================================== 2. work area
with st.container(key="work_area"):
    g1, g2 = st.columns(2)
    steps = bridge(c)
    fig = go.Figure(go.Waterfall(
        x=[s for s, _ in steps], y=[float(v) for _, v in steps],
        measure=["absolute"] + ["relative"] * (len(steps) - 2) + ["total"],
        text=[k(v) for _, v in steps], textposition="outside",
        increasing={"marker": {"color": T.POSITIVE}}, decreasing={"marker": {"color": T.NEGATIVE}},
        totals={"marker": {"color": T.DARK_NAVY}}, connector={"line": {"color": T.BORDER}}))
    fig.update_layout(title=dict(text="Cash bridge · 3 months (kEUR)", font=dict(size=14)), height=330,
                      margin=dict(l=10, r=10, t=40, b=10), plot_bgcolor="white", showlegend=False,
                      yaxis=dict(gridcolor=T.XL_GRID))
    g1.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    if any(s.startswith("Month transitions") for s, _ in steps):
        g1.caption("\\* Month transitions: each month starts from the sum of the companies' opening balances; the "
                   "difference to the previous month-end comes from IC differences.")
    xs = [f"{w.start:%d}–{w.end:%d %b}" for w in c.weeks]
    ys = [float(c.weekly["closing"][w]) for w in wk]
    line = go.Figure(go.Scatter(x=xs, y=ys, mode="lines+markers", line=dict(color=T.PRIMARY, width=2.5),
                                marker=dict(color=[T.NEGATIVE if y < 0 else T.PRIMARY for y in ys], size=7)))
    line.update_layout(title=dict(text="Weekly group cash balance (kEUR)", font=dict(size=14)), height=330,
                       margin=dict(l=10, r=10, t=40, b=10), plot_bgcolor="white",
                       yaxis=dict(gridcolor=T.XL_GRID, zeroline=True, zerolinecolor=T.MUTED, tickformat=",.0f",
                                  rangemode="tozero" if min(ys) >= 0 else "normal"))
    g2.plotly_chart(line, width="stretch", config={"displayModeBar": False})

    # ---- FC CONSO table ------------------------------------------------------------------
    if view == "Weeks":
        cols = []
        for p in c.periods:
            cols += [(w.code_week_period, f"{w.start:%d}–{w.end:%d}", p, "w")
                     for w in c.weeks if w.code_period_value == p]
            cols.append((p, "Total", p, "m"))
    else:
        cols = [(p, period_label(p, True), p, "m") for p in c.periods]
    cols.append(("TOTAL", "Σ 3 months", "", "t"))
    head1 = "".join(
        f'<th class="mh" colspan="{sum(1 for x in cols if x[2] == p)}">{period_label(p)}</th>' for p in c.periods
    ) + '<th class="mh"></th>'
    head2 = "".join(f'<th class="{t}">{html.escape(lbl)}</th>' for _, lbl, _, t in cols)
    body = []
    for key, label, kind in CONSO_ROWS:
        if kind == "section":
            body.append(f'<tr class="sec"><td colspan="{len(cols) + 1}">{html.escape(label)}</td></tr>')
            continue
        cells = []
        for col, _, _, t in cols:
            v = c.value(key, col)
            bad = (kind == "check" and v is not None and abs(v) > IC_TOLERANCE_KEUR) or (v is not None and v < 0)
            cells.append(f'<td class="{t}{" neg" if bad else ""}">{k(v)}</td>')
        body.append(f'<tr class="{kind}"><td class="lbl">{html.escape(label)}</td>{"".join(cells)}</tr>')
    st.markdown(f"""
<style>
.conso {{ width:100%; border-collapse:collapse; font-size:12.5px; font-variant-numeric:tabular-nums; }}
.conso th, .conso td {{ padding:4px 6px; border-bottom:1px solid {T.XL_GRID}; text-align:right; white-space:nowrap; }}
.conso th {{ background:{T.XL_WEEK_HEAD}; font-weight:600; color:{T.TEXT}; }}
.conso th.mh {{ text-align:center; border-left:2px solid #fff; }}
.conso th.m, .conso td.m {{ background:{T.XL_MONTH}; font-weight:600; }}
.conso th.t, .conso td.t {{ background:{T.PRIMARY_TINT}; font-weight:700; }}
.conso td.lbl {{ text-align:left; min-width:230px; }}
.conso tr.sec td {{ text-align:left; font-weight:700; letter-spacing:.03em; background:#fff; padding-top:10px;
                   color:{T.DARK_NAVY}; border-bottom:2px solid {T.DARK_NAVY}; }}
.conso tr.sum td {{ background:{T.XL_SUBTOTAL}; font-weight:600; }}
.conso tr.total td {{ background:{T.XL_TOTAL}; font-weight:700; }}
.conso tr.memo td {{ color:{T.MUTED}; font-style:italic; }}
.conso tr.check td {{ font-weight:600; }}
.conso td.neg {{ color:{T.NEGATIVE}; }}
</style>
<table class="conso"><thead><tr><th class="lbl" rowspan="2" style="text-align:left">kEUR</th>{head1}</tr>
<tr>{head2}</tr></thead><tbody>{"".join(body)}</tbody></table>""", unsafe_allow_html=True)
    st.caption(f"Sum of {n_ok} companies in kEUR (FX {rep.fx_reference}, one rate for all 3 months). IC payments are "
               "not contained in inflows and outflows; first week of each month = Σ companies' opening balances.")

    # ---- Excel export ----------------------------------------------------------------------
    def export() -> bytes:
        xcols = []
        for p in c.periods:
            xcols += [(w.code_week_period, f"{period_label(p, True)} {w.start:%d}–{w.end:%d}")
                      for w in c.weeks if w.code_period_value == p]
            xcols.append((p, period_label(p)))
        conso = pd.DataFrame([{"kEUR": label.upper() if kind == "section" else label,
                               **{h: (None if kind == "section" or c.value(key, col) is None
                                      else round(float(c.value(key, col)), 1)) for col, h in xcols}}
                              for key, label, kind in CONSO_ROWS])
        comp = companies_frame()
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            conso.to_excel(xw, sheet_name="FC CONSO", index=False, startrow=2)
            xw.sheets["FC CONSO"]["A1"] = f"Cash Flow FC consolidation (kEUR) – snapshot {rep.snapshot} ({rep.status})"
            comp.to_excel(xw, sheet_name="Companies", index=False)
            pd.DataFrame(rep.checks or [{"Severity": "", "Area": "", "Topic": "", "Message": "No findings"}]).to_excel(
                xw, sheet_name="Checks", index=False)
            pd.DataFrame({"Assumptions": ASSUMPTIONS}).to_excel(xw, sheet_name="Assumptions", index=False)
        return buf.getvalue()


def companies_frame() -> pd.DataFrame:
    rows = []
    first_wk = c.weeks[0].code_week_period
    for x in rep.companies:
        f = x.flows
        row = {"Company": x.business_unit, "Name": x.name, "Currency": x.currency,
               "Rate": None if x.rate is None else float(x.rate), "Status": x.status, "Revision": x.revision,
               f"Opening {period_label(c.periods[0], True)}": None}
        for p in c.periods:
            row[f"Closing {period_label(p, True)}"] = None
        row["Lowest week"], row["IC net 3 months"] = None, None
        if f is not None and x.status == "Consolidated":
            o = f.opening.get(first_wk)
            row[f"Opening {period_label(c.periods[0], True)}"] = None if o is None else round(float(o))
            for p in c.periods:
                v = f.month_closing.get(p)
                row[f"Closing {period_label(p, True)}"] = None if v is None else round(float(v))
            closings = [v for v in f.closing.values() if v is not None]
            row["Lowest week"] = round(float(min(closings))) if closings else None
            row["IC net 3 months"] = round(float(sum(f.weekly["ic_in"].values()) - sum(f.weekly["ic_out"].values())))
        rows.append(row)
    return pd.DataFrame(rows)


ASSUMPTIONS = [
    "Data: latest saved version of every company in the snapshot (when FINAL: the versions HQ marked final).",
    "Conversion: kEUR = local currency ÷ rate ÷ 1000; rate = IDL SK (BS) of the month before the snapshot "
    "(1 EUR = x LCY), one rate for all 3 months. Without a rate a company is not consolidated (no fallback).",
    "Line items: sum of the same subtotal of all companies, as the links in Excel “FC CONSO”.",
    "Intercompany: IC receipts (template rows 64/65) and IC payments (88/89) are not in Total Cash Inflow / Outflow; "
    "Check (= 0) = Σ IC inflow − Σ IC outflow, tolerance ±10 kEUR per month.",
    "Net Cash Movement = Total Cash Inflow − Total Cash Outflow + Borrowing (+) / repayment (−).",
    "Balances: first week of a month = Σ companies' opening balances; following weeks = previous group closing; "
    "Closing = Opening + Net. Month column: opening of the first week, closing of the last week.",
    "Company closing (per-company table) includes IC; the sum therefore differs from the group closing by IC differences.",
    "Empty is not zero: optional empty lines count as 0 in sums; companies without any values are listed, not summed.",
]

with st.container(key="work_area_export"):
    st.download_button("Download Excel (FC CONSO)", data=export(), type="primary",
                       file_name=f"Cashflow_FC_CONSO_{rep.snapshot}{'_FINAL' if rep.status == 'FINAL' else ''}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# =========================================================================== 3. details (collapsed)
with st.container(key="details"):
    with st.expander("Closing cash per company"):
        st.caption("Month-end balance of each company incl. IC, converted to kEUR. The sum differs from the group "
                   "closing by IC differences.")
        comp = companies_frame()
        ok = comp[comp["Status"] == "Consolidated"]
        num_cols = [col for col in comp.columns if col.startswith(("Opening", "Closing"))]
        total = {"Company": "Σ companies", **{col: ok[col].sum() for col in num_cols + ["IC net 3 months"]}}
        group = {"Company": "Group closing (FC CONSO)",
                 f"Opening {period_label(c.periods[0], True)}": round(float(c.monthly["opening"]["TOTAL"])),
                 **{f"Closing {period_label(p, True)}": round(float(c.monthly["closing"][p])) for p in c.periods}}
        diff = {"Company": "Difference group − Σ companies",
                **{col: (group.get(col) or 0) - (total.get(col) or 0) for col in num_cols}}
        st.dataframe(pd.concat([ok, pd.DataFrame([total, group, diff])], ignore_index=True)
                     .drop(columns=["Status", "Rate", "Currency"]), hide_index=True, width="stretch")

    with st.expander("Completeness & FX"):
        st.caption(f"All companies of the group with their forecast version and FX rate ({rep.fx_reference}). "
                   "Rate = 1 EUR in local currency.")
        st.dataframe(companies_frame()[["Company", "Name", "Currency", "Rate", "Revision", "Status"]],
                     hide_index=True, width="stretch",
                     column_config={"Rate": st.column_config.NumberColumn(format="%.4f")})

    title = f"Checks · {n_crit} critical, {n_chk} to check, {sum(ch['Severity'] == INFO for ch in rep.checks)} info"
    with st.expander(title):
        st.caption("Critical: result wrong or incomplete. Check: clarify with the company. Info: no impact on the result.")
        if rep.checks:
            st.dataframe(pd.DataFrame(rep.checks), hide_index=True, width="stretch")
        else:
            st.success("No findings.")

    with st.expander("Company detail"):
        ok_rows = rep.consolidated
        bu = st.selectbox("Company", [x.business_unit for x in ok_rows],
                          format_func=lambda b: f"{b} · {next(x.name for x in ok_rows if x.business_unit == b)}")
        x = next(r for r in ok_rows if r.business_unit == bu)
        one = consolidate([x.flows], c.weeks)
        for wc in one.weekly["opening"]:                    # company's own balances (incl. IC)
            one.weekly["opening"][wc] = x.flows.opening[wc] or Decimal(0)
            one.weekly["closing"][wc] = x.flows.closing[wc] or Decimal(0)
        for p in one.periods:
            wcs = [w.code_week_period for w in one.weeks if w.code_period_value == p]
            one.monthly["opening"][p], one.monthly["closing"][p] = one.weekly["opening"][wcs[0]], one.weekly["closing"][wcs[-1]]
        one.monthly["opening"]["TOTAL"] = one.weekly["opening"][wk[0]]
        one.monthly["closing"]["TOTAL"] = one.weekly["closing"][wk[-1]]
        mcols = [p for p in c.periods] + ["TOTAL"]
        st.dataframe(pd.DataFrame([{"kEUR": label, **{(period_label(m, True) if m != "TOTAL" else "Σ 3 months"):
                                                      (None if one.value(key, m) is None else round(float(one.value(key, m))))
                                                      for m in mcols}}
                                   for key, label, kind in CONSO_ROWS if kind != "section"]),
                     hide_index=True, width="stretch")
        st.caption(f"Rate {x.rate} {x.currency} per EUR. Opening / closing = the company's own balances incl. IC.")

    with st.expander("Assumptions & calculation logic"):
        st.markdown("\n".join(f"{i}. {a}" for i, a in enumerate(ASSUMPTIONS, 1)))
