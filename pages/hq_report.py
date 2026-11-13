"""HQ report – consolidated 3-month cash-flow forecast of the group in kEUR (replica of Excel "FC CONSO").

Layout: controls · hero · KPI cards · charts · tabs (FC CONSO, companies, completeness & FX, checks,
company detail, assumptions). Visual building blocks: components/report_ui.py.
"""
from __future__ import annotations

import io
from decimal import Decimal

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from components import report_ui as U
from components import theme as T
from components.forecast_grid import period_label
from domain.consolidation import CONSO_ROWS, IC_TOLERANCE_KEUR, Consolidation, bridge, consolidate
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

st.markdown(U.CSS, unsafe_allow_html=True)
st.markdown(f"""<style>
.st-key-rp_c1, .st-key-rp_c2 {{ background:#fff; border:1px solid #E3EAF5; border-radius:18px; padding:14px 16px 4px;
  box-shadow:0 10px 30px -20px rgba(23,38,94,.35); }}
.st-key-rp_ctl {{ margin-bottom:6px; }}
</style>""", unsafe_allow_html=True)

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
    "Company closing (per-company view) includes IC; the sum therefore differs from the group closing by IC differences.",
    "Companies marked “no delivery expected” (V013, V014) are listed but not reported as missing.",
    "Empty is not zero: optional empty lines count as 0 in sums; companies without any values are listed, not summed.",
]

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


# =========================================================================== controls
with st.container(key="rp_ctl"):
    c1, c2, _, c4 = st.columns([1.2, 1.4, 4.4, 0.9], vertical_alignment="bottom")
    snap = c1.selectbox("Snapshot", snapshots, index=snapshots.index(default), key="rep_snap",
                        format_func=lambda s: f"{s}  ·  FINAL" if s in finals else s)
    view = c2.segmented_control("Show", ["Weeks", "Months"], default="Weeks", key="rep_view") or "Weeks"
    if c4.button("Refresh", width="stretch", icon=":material/refresh:", help="Reload the numbers from Keboola"):
        st.session_state.pop("final_snapshots", None)
        load_report(snap, force=True)
        st.rerun()
rep = load_report(snap)
c: Consolidation | None = rep.conso
n_exp = sum(1 for x in rep.companies if x.expected)
n_ok = len(rep.consolidated)
n_crit = sum(ch["Severity"] == CRITICAL for ch in rep.checks)
n_chk = sum(ch["Severity"] == CHECK for ch in rep.checks)
n_info = sum(ch["Severity"] == INFO for ch in rep.checks)

# =========================================================================== hero
p_from, p_to = period_label(rep.periods[0]), period_label(rep.periods[-1])
tags = [U.tag("FINAL" if rep.status == "FINAL" else "NOT FINAL – numbers can still change",
              "final" if rep.status == "FINAL" else "open")]
if rep.fx_reference:
    tags.append(U.tag(f"FX {rep.fx_reference}"))
tags.append(U.tag(f"{n_ok} of {n_exp} companies consolidated"))
if rep.status == "FINAL" and rep.status_by:
    tags.append(U.tag(f"marked by {rep.status_by} · {rep.status_at[:10]}"))
right = ""
if c is not None:
    _cl = [float(c.weekly["closing"][w.code_week_period]) for w in c.weeks]
    _chg = c.monthly["closing"]["TOTAL"] - c.monthly["opening"]["TOTAL"]
    right = (f'<div class="rp-hero-r"><div class="lb">Group closing · {period_label(rep.periods[-1], True)}</div>'
             f'<div class="big">{U.k(c.monthly["closing"]["TOTAL"], False)}<small>kEUR</small></div>'
             f'<div class="chg">{"▲" if _chg >= 0 else "▼"} {U.k(abs(_chg), False)} vs opening</div>'
             f'{U.sparkline(_cl, 190, 42, "#7CC0FF", "")}</div>')
st.markdown(U.hero(f"HQ report · snapshot {rep.snapshot}", "Group cash flow forecast",
                   f"{p_from.split()[0]} – {p_to}  ·  kEUR  ·  structure of Excel “FC CONSO”, intercompany eliminated",
                   tags, right), unsafe_allow_html=True)

if c is None:
    st.warning("No company could be consolidated in this snapshot (no forecasts or no FX rates).")
    st.stop()

wk = [w.code_week_period for w in c.weeks]
closing_series = [float(c.weekly["closing"][w]) for w in wk]

# =========================================================================== KPI cards
low_w = min(c.weeks, key=lambda w: c.weekly["closing"][w.code_week_period])
low = c.weekly["closing"][low_w.code_week_period]
net = c.monthly["net"]["TOTAL"]
opening = c.monthly["opening"]["TOTAL"]
closing = c.monthly["closing"]["TOTAL"]
ic_months = [abs(c.monthly["ic_diff"][p]) for p in c.periods]
ic_total = c.monthly["ic_diff"]["TOTAL"]
pct = (net / abs(opening) * 100) if opening else None
delta = (f'<span class="rp-delta {"rp-up" if net >= 0 else "rp-down"}">{"▲" if net >= 0 else "▼"} '
         f'{abs(pct):.0f} %</span>' if pct is not None else "")
cum, acc = [], 0.0
for w in wk:
    acc += float(c.weekly["net"][w])
    cum.append(acc)
ic_worst = max(ic_months) if ic_months else Decimal(0)
ic_col = T.NEGATIVE if ic_worst > IC_TOLERANCE_KEUR else T.POSITIVE
cards = [
    U.kpi(f"Opening · {period_label(c.periods[0], True)}", U.k(opening, False),
          f"Σ companies · {c.weeks[0].start:%d}–{c.weeks[0].end:%d %b}", "wallet", T.PRIMARY, delay=0),
    U.kpi("Net cash movement", U.k(net, False), f"{delta} 3 months · excl. IC", "flow",
          T.POSITIVE if net >= 0 else T.NEGATIVE,
          extra=U.sparkline(cum, color=T.POSITIVE if net >= 0 else T.NEGATIVE), delay=.05),
    U.kpi(f"Closing · {period_label(c.periods[-1], True)}", U.k(closing, False), "group, end of horizon", "flag",
          T.DARK_NAVY, extra=U.sparkline(closing_series, color=T.DARK_NAVY), delay=.1),
    U.kpi("Lowest weekly cash", U.k(low, False), f"{low_w.start:%d}–{low_w.end:%d %b %Y}", "low",
          T.NEGATIVE if low < 0 else "#0EA5E9", delay=.15),
    U.kpi("IC difference", U.k(ic_total, False), f"worst month {U.k(ic_worst, False)} · limit ±{IC_TOLERANCE_KEUR:.0f}",
          "link", ic_col, extra=U.gauge(float(ic_worst / IC_TOLERANCE_KEUR) if IC_TOLERANCE_KEUR else 0, ic_col),
          delay=.2),
    U.kpi("Companies", f"{n_ok}/{n_exp}", f"{n_crit} critical · {n_chk} to check", "grid",
          T.NEGATIVE if n_crit else (T.WARNING if n_chk else T.POSITIVE), unit="",
          extra=U.ring(n_ok / n_exp if n_exp else 0, T.NEGATIVE if n_crit else T.POSITIVE), delay=.25),
]
st.markdown(f'<div class="rp-kpis">{"".join(cards)}</div>', unsafe_allow_html=True)

# =========================================================================== charts
FONT = dict(family="Source Sans Pro, Inter, system-ui, sans-serif", color=T.TEXT, size=12)
HOVER = dict(bgcolor="#0B1640", bordercolor="#0B1640", font=dict(color="#fff", size=12))
g1, g2 = st.columns([1, 1.15])
with g1.container(key="rp_c1"):
    steps = bridge(c)
    st.markdown(U.card_head("Cash bridge", "3 months · kEUR"), unsafe_allow_html=True)
    fig = go.Figure(go.Waterfall(
        x=[s.replace(" & ", " &<br>").replace("Month transitions*", "Month<br>transitions*") for s, _ in steps],
        y=[float(v) for _, v in steps],
        measure=["absolute"] + ["relative"] * (len(steps) - 2) + ["total"],
        text=[U.k(v, False) for _, v in steps], textposition="outside", textfont=dict(size=12, color=T.TEXT),
        increasing={"marker": {"color": "#10B981"}}, decreasing={"marker": {"color": "#F43F5E"}},
        totals={"marker": {"color": T.DARK_NAVY}}, connector={"line": {"color": "#CBD5E1", "dash": "dot", "width": 1}},
        hovertemplate="%{x}<br><b>%{y:,.0f} kEUR</b><extra></extra>"))
    fig.update_layout(height=300, margin=dict(l=4, r=4, t=18, b=4), plot_bgcolor="rgba(0,0,0,0)",
                      paper_bgcolor="rgba(0,0,0,0)", font=FONT, hoverlabel=HOVER, showlegend=False, bargap=.38,
                      yaxis=dict(gridcolor="#EEF2F7", zeroline=False, tickformat=",.0f", showline=False),
                      xaxis=dict(showline=False, tickfont=dict(color=T.MUTED, size=11), tickangle=0))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    if any(s.startswith("Month transitions") for s, _ in steps):
        st.caption("\\* Month transitions: each month restarts from the companies' opening balances "
                   "(difference = IC mismatches).")

with g2.container(key="rp_c2"):
    st.markdown(U.card_head("Weekly group cash balance", "closing per week · kEUR"), unsafe_allow_html=True)
    xs = [f"{w.start:%d}–{w.end:%d} {w.end:%b}" for w in c.weeks]
    i_low = closing_series.index(min(closing_series))
    line = go.Figure()
    lo_, hi_ = min(closing_series), max(closing_series)
    pad = max((hi_ - lo_) * 0.25, abs(hi_) * 0.02, 1.0)
    y0, y1 = (min(lo_ - pad, 0) if lo_ < 0 else lo_ - pad), hi_ + pad * 1.8      # room for the labels
    line.add_trace(go.Scatter(x=xs, y=[y0] * len(xs), mode="lines", line=dict(width=0), hoverinfo="skip"))
    line.add_trace(go.Scatter(x=xs, y=closing_series, mode="lines", line=dict(width=0), fill="tonexty",
                              fillcolor="rgba(30,144,255,0.10)", hoverinfo="skip"))
    line.add_trace(go.Scatter(
        x=xs, y=closing_series, mode="lines+markers", line=dict(color=T.PRIMARY, width=2.5, shape="linear",
                                                                  smoothing=.6),
        marker=dict(size=8, color=[T.NEGATIVE if v < 0 else T.PRIMARY for v in closing_series],
                    line=dict(color="#fff", width=2)),
        hovertemplate="%{x}<br><b>%{y:,.0f} kEUR</b><extra></extra>"))
    for i, txt in {0: "start", len(xs) - 1: "end", i_low: "lowest"}.items():
        line.add_annotation(x=xs[i], y=closing_series[i], text=f"<b>{U.k(Decimal(str(closing_series[i])), False)}</b>"
                            f"<br><span style='color:{T.MUTED}'>{txt}</span>", showarrow=False, yshift=22,
                            font=dict(size=11, color=T.TEXT), bgcolor="rgba(255,255,255,.85)")
    for p in c.periods[1:]:                                     # month separators
        first = next(i for i, w in enumerate(c.weeks) if w.code_period_value == p)
        line.add_vline(x=first - 0.5, line=dict(color="#E2E8F0", width=1, dash="dot"))
    line.update_layout(height=300, margin=dict(l=4, r=4, t=18, b=4), plot_bgcolor="rgba(0,0,0,0)",
                       paper_bgcolor="rgba(0,0,0,0)", font=FONT, hoverlabel=HOVER, showlegend=False,
                       hovermode="closest",
                       yaxis=dict(gridcolor="#EEF2F7", tickformat=",.0f", zeroline=True, zerolinecolor="#94A3B8",
                                  range=[y0, y1]),
                       xaxis=dict(tickfont=dict(color=T.MUTED, size=10.5), tickangle=0, showgrid=False))
    st.plotly_chart(line, width="stretch", config={"displayModeBar": False})


# =========================================================================== table renderer
def columns(cons: Consolidation) -> list[tuple[str, str, str, str]]:
    cols = []
    if view == "Weeks":
        for p in cons.periods:
            cols += [(w.code_week_period, f"{w.start:%d}–{w.end:%d}", p, "")
                     for w in cons.weeks if w.code_period_value == p]
            cols.append((p, "Total", p, "m"))
    else:
        cols = [(p, period_label(p, True), p, "m") for p in cons.periods]
    cols.append(("TOTAL", "Σ 3 months", "", "t"))
    return cols


def conso_table(cons: Consolidation, factor: Decimal = Decimal(1), unit: str = "kEUR") -> str:
    cols = columns(cons)
    head1 = f'<tr><th class="lbl" rowspan="2">{unit}</th>' + "".join(
        f'<th class="mh" colspan="{sum(1 for x in cols if x[2] == p)}">{period_label(p)}</th>' for p in cons.periods
    ) + '<th class="mh t"></th></tr>'
    head2 = "<tr>" + "".join(f'<th class="{t}">{U.E(lbl)}</th>' for _, lbl, _, t in cols) + "</tr>"
    body = []
    for key, label, kind in CONSO_ROWS:
        if kind == "section":
            body.append(f'<tr class="sec"><td class="lbl" colspan="{len(cols) + 1}">{U.E(label)}</td></tr>')
            continue
        cells = []
        for col, _, _, t in cols:
            v = cons.value(key, col)
            v = None if v is None else v * factor
            cells.append(U.num_cell(v, t))
        body.append(f'<tr class="{kind}"><td class="lbl">{U.E(label)}</td>{"".join(cells)}</tr>')
    return U.table([head1, head2], body)


def companies_frame() -> pd.DataFrame:
    rows = []
    first_wk = wk[0]
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
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        conso.to_excel(xw, sheet_name="FC CONSO", index=False, startrow=2)
        xw.sheets["FC CONSO"]["A1"] = f"Cash Flow FC consolidation (kEUR) – snapshot {rep.snapshot} ({rep.status})"
        companies_frame().to_excel(xw, sheet_name="Companies", index=False)
        pd.DataFrame([{**r, "Rate": None if r["Rate"] is None else float(r["Rate"]),
                       "Companies": ", ".join(r["Companies"])} for r in rep.rates_by_currency()]).to_excel(
            xw, sheet_name="FX rates", index=False)
        pd.DataFrame(rep.checks or [{"Severity": "", "Area": "", "Topic": "", "Message": "No findings"}]).to_excel(
            xw, sheet_name="Checks", index=False)
        pd.DataFrame({"Assumptions": ASSUMPTIONS}).to_excel(xw, sheet_name="Assumptions", index=False)
    return buf.getvalue()


# =========================================================================== tabs
st.write("")
t_conso, t_comp, t_fx, t_chk, t_det, t_ass = st.tabs([
    "FC CONSO", "Companies", "Completeness & FX",
    f"Checks · {n_crit + n_chk}" if (n_crit + n_chk) else "Checks", "Company detail", "Assumptions"])

with t_conso:
    h1, h2 = st.columns([5, 1.3], vertical_alignment="center")
    h1.caption(f"Sum of {n_ok} companies in kEUR · FX {rep.fx_reference} (one rate for all 3 months) · IC payments "
               "are not in inflows / outflows · first week of each month = Σ companies' opening balances.")
    h2.download_button("Excel export", data=export(), type="primary", icon=":material/download:", width="stretch",
                       file_name=f"Cashflow_FC_CONSO_{rep.snapshot}{'_FINAL' if rep.status == 'FINAL' else ''}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.markdown(conso_table(c), unsafe_allow_html=True)

with t_comp:
    st.caption("Month-end balance of each company incl. IC, in kEUR. The sum differs from the group closing by IC "
               "differences.")
    ok = [x for x in rep.consolidated]
    last = c.periods[-1]
    max_abs = max([abs(x.flows.month_closing.get(last) or 0) for x in ok] + [Decimal(1)])
    head = ('<tr><th class="lbl">Company</th><th>Trend (weekly)</th>'
            f'<th>Opening {period_label(c.periods[0], True)}</th>'
            + "".join(f'<th class="m">{period_label(p, True)}</th>' for p in c.periods)
            + '<th>Lowest week</th><th>IC net</th></tr>')
    body = []
    for x in sorted(ok, key=lambda r: -(r.flows.month_closing.get(last) or 0)):
        f = x.flows
        series = [float(f.closing[w] or 0) for w in wk]
        lc = f.month_closing.get(last) or Decimal(0)
        bar = (f'<span class="rp-bar" style="width:{max(2, float(abs(lc) / max_abs) * 70):.0f}px;'
               f'background:{T.NEGATIVE if lc < 0 else T.PRIMARY}"></span>')
        cells = "".join(U.num_cell(f.month_closing.get(p), "m") for p in c.periods[:-1])
        cells += f'<td class="m{" neg" if lc < 0 else ""}">{U.k(lc)}{bar}</td>'
        lowest = min((v for v in f.closing.values() if v is not None), default=None)
        icn = sum(f.weekly["ic_in"].values()) - sum(f.weekly["ic_out"].values())
        body.append(f'<tr><td class="lbl"><b>{U.E(x.business_unit)}</b> <span style="color:{T.MUTED}">'
                    f'{U.E(x.name)}</span></td><td>{U.sparkline(series, 110, 26, T.NEGATIVE if min(series) < 0 else T.PRIMARY, "")}'
                    f'</td>{U.num_cell(f.opening.get(wk[0]))}{cells}{U.num_cell(lowest)}{U.num_cell(icn)}</tr>')
    sum_open = sum((x.flows.opening.get(wk[0]) or 0 for x in ok), Decimal(0))
    sums = {p: sum((x.flows.month_closing.get(p) or 0 for x in ok), Decimal(0)) for p in c.periods}
    body.append('<tr class="total"><td class="lbl">Σ companies</td><td></td>' + U.num_cell(sum_open)
                + "".join(U.num_cell(sums[p], "m") for p in c.periods) + "<td></td><td></td></tr>")
    body.append('<tr class="sum"><td class="lbl">Group closing (FC CONSO)</td><td></td>'
                + U.num_cell(c.monthly["opening"]["TOTAL"])
                + "".join(U.num_cell(c.monthly["closing"][p], "m") for p in c.periods) + "<td></td><td></td></tr>")
    body.append('<tr class="memo"><td class="lbl">Difference group − Σ companies</td><td></td>'
                + U.num_cell(c.monthly["opening"]["TOTAL"] - sum_open)
                + "".join(U.num_cell(c.monthly["closing"][p] - sums[p], "m") for p in c.periods)
                + "<td></td><td></td></tr>")
    st.markdown(U.table([head], body), unsafe_allow_html=True)

with t_fx:
    st.caption(f"All companies of the group · FX {rep.fx_reference} · rate = 1 EUR in local currency.")
    tiles = "".join(U.tile(x.business_unit, x.name, x.currency,
                           "–" if x.rate is None else ("1" if x.currency == "EUR" else f"{x.rate:,.4f}"),
                           x.status, x.revision) for x in rep.companies)
    st.markdown(f'<div class="rp-tiles">{tiles}</div>', unsafe_allow_html=True)
    st.write("")
    st.markdown(U.card_head("Rates by currency", "IDL SK (BS) · used for all companies of that currency"),
                unsafe_allow_html=True)
    rows = []
    for r in rep.rates_by_currency():
        state = ("Used" if r["Used"] else "Not used") if r["Rate"] is not None else "Missing"
        color = {"Used": "#047857", "Not used": T.MUTED, "Missing": "#B91C1C"}[state]
        rate = "–" if r["Rate"] is None else ("1" if r["Currency"] == "EUR" else f"{r['Rate']:,.4f}")
        rows.append(f'<tr><td class="lbl"><b>{r["Currency"]}</b></td><td>{rate}</td>'
                    f'<td style="text-align:left">{U.E(", ".join(r["Companies"]))}</td>'
                    f'<td><span class="rp-st" style="color:{color};background:#F4F7FC">● {state}</span></td></tr>')
    st.markdown(U.table(['<tr><th class="lbl">Currency</th><th>1 EUR =</th><th style="text-align:left">Companies</th>'
                         '<th>Status</th></tr>'], rows), unsafe_allow_html=True)

with t_chk:
    opts = [f"All · {len(rep.checks)}", f"Critical · {n_crit}", f"Check · {n_chk}", f"Info · {n_info}"]
    pick = st.segmented_control("Severity", opts, default=opts[0], key="rep_chk", label_visibility="collapsed") or opts[0]
    sev = {1: CRITICAL, 2: CHECK, 3: INFO}.get(opts.index(pick))
    shown = [ch for ch in rep.checks if sev is None or ch["Severity"] == sev]
    st.caption("Critical: result wrong or incomplete · Check: clarify with the company · Info: no impact on the result.")
    if shown:
        st.markdown("".join(U.finding(ch["Severity"], ch["Area"], ch["Topic"], ch["Message"]) for ch in shown),
                    unsafe_allow_html=True)
    else:
        st.markdown('<div class="rp-ok">✓ No findings in this category.</div>', unsafe_allow_html=True)

with t_det:
    d1, d2, _ = st.columns([2.2, 1.4, 3])
    names = {x.business_unit: x for x in rep.consolidated}
    bu = d1.selectbox("Company", list(names), format_func=lambda b: f"{b} · {names[b].name}", key="rep_det_bu")
    x = names[bu]
    units = ["kEUR"] + ([f"k{x.currency}"] if x.currency != "EUR" else [])
    unit = d2.segmented_control("Unit", units, default="kEUR", key=f"rep_det_unit::{bu}") or "kEUR"
    one = consolidate([x.flows], c.weeks)
    for w_ in wk:                                           # the company's own balances (incl. IC)
        one.weekly["opening"][w_] = x.flows.opening[w_] or Decimal(0)
        one.weekly["closing"][w_] = x.flows.closing[w_] or Decimal(0)
    for p in one.periods:
        wcs = [w.code_week_period for w in one.weeks if w.code_period_value == p]
        one.monthly["opening"][p], one.monthly["closing"][p] = one.weekly["opening"][wcs[0]], one.weekly["closing"][wcs[-1]]
    one.monthly["opening"]["TOTAL"], one.monthly["closing"]["TOTAL"] = one.weekly["opening"][wk[0]], one.weekly["closing"][wk[-1]]
    factor = x.rate if unit != "kEUR" else Decimal(1)
    st.caption(f"{x.name} · rate {x.rate} {x.currency} per EUR · revision {x.revision} · opening / closing = the "
               "company's own balances incl. IC.")
    st.markdown(conso_table(one, factor, unit), unsafe_allow_html=True)

with t_ass:
    st.markdown('<div class="rp-card"><ol class="rp-steps">' + "".join(f"<li>{U.E(a)}</li>" for a in ASSUMPTIONS)
                + "</ol></div>", unsafe_allow_html=True)
