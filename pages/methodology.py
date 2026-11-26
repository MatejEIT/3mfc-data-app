"""Guide & methodology – how a company prepares and submits its 3-month cash flow forecast (step by step),
definitions of the terms used in the app and the calculation rules the application enforces."""
import html

import streamlit as st

from components import theme as T

E = html.escape

st.markdown(f"""<style>
.gd-hero {{ background:linear-gradient(120deg,#0B1640,{T.DARK_NAVY} 55%,#1A4FA8); color:#fff; border-radius:20px;
  padding:26px 30px; margin:4px 0 18px; box-shadow:0 18px 40px -26px rgba(23,38,94,.7); }}
.gd-hero .eb {{ font-size:11px; letter-spacing:.16em; text-transform:uppercase; color:#9CC7FF; font-weight:700; }}
.gd-hero h2 {{ margin:6px 0 6px; font-size:28px; font-weight:800; color:#fff; }}
.gd-hero p {{ margin:0; color:#D6E4FF; font-size:14.5px; max-width:900px; }}
.gd-flow {{ display:grid; grid-template-columns:repeat(6,1fr); gap:10px; margin:8px 0 6px; }}
@media (max-width:1100px) {{ .gd-flow {{ grid-template-columns:repeat(3,1fr); }} }}
@media (max-width:640px) {{ .gd-flow {{ grid-template-columns:1fr; }} }}
.gd-step {{ position:relative; background:#fff; border:1px solid #E3EAF5; border-radius:16px; padding:14px 14px 12px;
  box-shadow:0 10px 26px -22px rgba(23,38,94,.45); }}
.gd-step:not(:last-child):after {{ content:"›"; position:absolute; right:-10px; top:38%; font-size:22px; color:#9AB3DA;
  font-weight:700; }}
@media (max-width:1100px) {{ .gd-step:after {{ display:none; }} }}
.gd-num {{ width:30px; height:30px; border-radius:50%; display:inline-flex; align-items:center; justify-content:center;
  font-weight:800; font-size:14px; color:#fff; background:{T.PRIMARY}; }}
.gd-num.hq {{ background:{T.DARK_NAVY}; }}
.gd-who {{ float:right; font-size:10.5px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; padding:3px 8px;
  border-radius:999px; background:#E6F2FF; color:#1A5FB4; }}
.gd-who.hq {{ background:#E9ECF5; color:{T.DARK_NAVY}; }}
.gd-step h4 {{ margin:10px 0 4px; font-size:14.5px; color:{T.TEXT}; }}
.gd-step p {{ margin:0; font-size:12.8px; color:{T.MUTED}; line-height:1.45; }}
.gd-card {{ background:#fff; border:1px solid #E3EAF5; border-radius:16px; padding:18px 20px; margin:10px 0;
  box-shadow:0 10px 26px -24px rgba(23,38,94,.45); }}
.gd-card h3 {{ margin:0 0 10px; font-size:17px; display:flex; align-items:center; gap:10px; }}
.gd-card h3 .gd-num {{ flex:none; margin-right:10px; }}
.gd-cols {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
@media (max-width:800px) {{ .gd-cols {{ grid-template-columns:1fr; }} }}
.gd-cols h5 {{ margin:0 0 6px; font-size:12px; letter-spacing:.1em; text-transform:uppercase; color:{T.MUTED}; }}
.gd-cols ul {{ margin:0; padding-left:18px; }} .gd-cols li {{ margin:3px 0; font-size:13.5px; }}
.gd-tip {{ margin-top:12px; padding:10px 14px; border-radius:12px; background:#FFF8E6; border:1px solid #FBE3A6;
  font-size:13px; color:#7A4B00; }}
.gd-legend {{ display:flex; flex-wrap:wrap; gap:10px; margin:8px 0 2px; }}
.gd-legend span {{ display:inline-flex; align-items:center; gap:8px; font-size:13px; padding:6px 10px; border:1px solid #E3EAF5;
  border-radius:10px; background:#fff; }}
.gd-legend i {{ width:22px; height:16px; border-radius:4px; border:1px solid #CBD5E1; display:inline-block; }}
.gd-ic {{ display:grid; grid-template-columns:1fr 150px 1fr; gap:14px; align-items:center; margin:6px 0 4px; }}
@media (max-width:800px) {{ .gd-ic {{ grid-template-columns:1fr; }} }}
.gd-co {{ border:1px solid #E3EAF5; border-radius:14px; overflow:hidden; background:#fff; }}
.gd-co .t {{ padding:8px 12px; font-weight:750; font-size:13.5px; background:#F6F9FE; border-bottom:1px solid #E3EAF5; }}
.gd-co .r {{ display:flex; justify-content:space-between; padding:7px 12px; font-size:13px; border-bottom:1px solid #F0F3F8; }}
.gd-co .r:last-child {{ border-bottom:none; }}
.gd-co .in {{ background:{T.XL_INPUT}; }} .gd-co .calc {{ background:{T.XL_SUBTOTAL}; color:#475569; }}
.gd-arrow {{ text-align:center; font-size:12.5px; color:{T.MUTED}; }}
.gd-arrow b {{ display:block; font-size:30px; color:{T.PRIMARY}; line-height:1; }}
.gd-def {{ display:grid; grid-template-columns:220px 1fr; border-top:1px solid #EEF2F7; }}
.gd-def div {{ padding:9px 6px; font-size:13.5px; border-bottom:1px solid #EEF2F7; }}
.gd-def div:nth-child(odd) {{ font-weight:700; color:{T.DARK_NAVY}; }}
@media (max-width:700px) {{ .gd-def {{ grid-template-columns:1fr; }} .gd-def div:nth-child(odd) {{ border-bottom:none; padding-bottom:0; }} }}
.gd-sev {{ display:inline-block; font-size:11px; font-weight:750; padding:2px 8px; border-radius:999px; }}
.gd-sev.b {{ background:#FEE2E2; color:#B91C1C; }} .gd-sev.w {{ background:#FEF3C7; color:#92400E; }}
.gd-sev.i {{ background:#E6F2FF; color:#1A5FB4; }}
</style>""", unsafe_allow_html=True)

st.markdown("""<div class="gd-hero"><div class="eb">Guide &amp; methodology</div>
<h2>Your 3-month cash flow forecast – step by step</h2>
<p>Every month each company forecasts its cash for the next three months, week by week, in its local currency.
This page explains the process, what each colour and term means and how the numbers are calculated.</p></div>""",
            unsafe_allow_html=True)

STEPS = [
    ("1", "HQ", "New snapshot", "HQ opens the month's snapshot. Your forecast is pre-filled from last month."),
    ("2", "You", "Open your forecast", "Forecast Editor → choose your company and the snapshot."),
    ("3", "You", "Update the weeks", "Check the pre-filled numbers, type the new ones, name customers / projects."),
    ("4", "You", "Intercompany", "Enter only the IC money you send – with its receiver."),
    ("5", "You", "Validate & submit", "Fix blocking issues, review the warnings, submit with a comment."),
    ("6", "HQ", "Review & report", "HQ checks, adjusts if needed and consolidates the group in kEUR."),
]
flow = "".join(
    f'<div class="gd-step"><span class="gd-who{" hq" if who == "HQ" else ""}">{E(who)}</span>'
    f'<span class="gd-num{" hq" if who == "HQ" else ""}">{n}</span><h4>{E(t)}</h4><p>{E(d)}</p></div>'
    for n, who, t, d in STEPS)
st.markdown(f'<div class="gd-flow">{flow}</div>', unsafe_allow_html=True)

t_guide, t_ic, t_val, t_def, t_meth = st.tabs(
    ["Step by step", "Intercompany", "Validations", "Definitions", "Methodology"])


def card(num: str, title: str, you: list[str], app: list[str], tip: str = "", hq: bool = False) -> str:
    li = lambda xs: "".join(f"<li>{x}</li>" for x in xs)
    tip_html = f'<div class="gd-tip">💡 {tip}</div>' if tip else ""
    return (f'<div class="gd-card"><h3><span class="gd-num{" hq" if hq else ""}">{num}</span>{E(title)}</h3>'
            f'<div class="gd-cols"><div><h5>{"HQ does" if hq else "You do"}</h5><ul>{li(you)}</ul></div>'
            f'<div><h5>The app does</h5><ul>{li(app)}</ul></div></div>{tip_html}</div>')


# =========================================================================== step by step
with t_guide:
    st.markdown(card(
        "1", "HQ opens the new snapshot",
        ["creates the snapshot of the month (e.g. <b>202612</b> = forecast for December, January and February)",
         "confirms the week calendar of the new month"],
        ["copies your last forecast into the new snapshot (<b>rolling 2+1</b>): the two months that stay in the "
         "horizon keep your numbers, the new 3rd month is pre-filled from the month before and marked "
         "<b>orange</b> for review",
         "sets the opening cash = the calculated closing cash of the week before"], hq=True), unsafe_allow_html=True)
    st.markdown(card(
        "2", "Open your forecast",
        ["go to <b>Forecast Editor</b>, choose your <b>company</b> and the <b>snapshot</b>",
         "pick the view under <b>Show</b>: all weeks, one month (bigger columns) or month totals only"],
        ["shows the status: <b>DRAFT</b> (you can edit) or <b>SUBMITTED</b> (read-only for you)",
         "shows the FX rate of the snapshot and the revision you are working on"],
        "Values are in thousands of your local currency (e.g. kUSD). The month columns are always calculated "
        "from the weeks."), unsafe_allow_html=True)
    st.markdown(card(
        "3", "Update the weeks",
        ["type your numbers into the <b>light-yellow cells</b> – inflows and outflows as <b>positive</b> numbers",
         "type the customer / project name in the first column of the detail rows (one free row per list is "
         "shown; switch on <b>All template rows</b> for more)",
         "review the <b>orange</b> pre-filled 3rd month – keep or change it",
         "click <b>Save</b> as often as you like – a draft can always be saved"],
        ["recalculates subtotals, totals, net cash movement and the weekly cash balance immediately",
         "shows unsaved cells in blue; <b>Discard</b> throws them away, <b>Reload</b> loads the latest saved "
         "version",
         "stores every saved change with who / when / old / new value (<b>Change history</b>)"],
        "Leave a cell empty when there is nothing to forecast; type <b>0</b> only when you really expect zero. "
        "Empty and 0 are kept apart."), unsafe_allow_html=True)
    st.markdown("**Colours in the forecast grid**")
    legend = [("Input – type here", T.XL_INPUT), ("Calculated", T.XL_SUBTOTAL), ("Month total", T.XL_MONTH),
              ("Pre-filled 3rd month – please review", T.CARRY_BG), ("Changed by HQ", T.HQ_BG),
              ("Unsaved change", T.PENDING_BG)]
    st.markdown('<div class="gd-legend">' + "".join(f'<span><i style="background:{c}"></i>{E(t)}</span>'
                                                    for t, c in legend) + "</div>", unsafe_allow_html=True)
    st.markdown(card(
        "4", "Enter your intercompany payments",
        ["enter only the IC money you <b>send</b>: row <b>“IC (VIDE-GMBH)”</b> = payment to VIDE GmbH, rows "
         "<b>“IC to &lt;company&gt;”</b> = payment to that company (add one with <b>＋ add IC partner</b>)"],
        ["fills your <b>IC inflow</b> automatically from what the other companies send you (see the "
         "<b>Intercompany</b> tab)",
         "shows at the top of the editor whose payments are already included and who has not submitted yet"],
        "Valid from snapshot 202612. Earlier snapshots keep the IC inflow the companies entered themselves."),
        unsafe_allow_html=True)
    st.markdown(card(
        "5", "Validate & submit",
        ["click <b>Validate &amp; submit</b>",
         "fix the <b>blocking</b> issues (red) – otherwise you cannot submit",
         "read the <b>warnings</b>: go back with <b>Edit</b>, or submit anyway and explain in the <b>Comment</b>",
         "click <b>Submit forecast</b>"],
        ["checks your forecast (see the <b>Validations</b> tab)",
         "stores the submitted version unchangeable, together with your comment and the warnings you accepted",
         "uses your submitted IC outflow to fill your partners' IC inflow"],
        "After submitting, the forecast is read-only for you. If something must change, contact HQ – HQ can "
        "adjust it (every adjustment is recorded)."), unsafe_allow_html=True)
    st.markdown(card(
        "6", "HQ review and group report",
        ["follows the status of all companies in <b>HQ Workspace</b>",
         "adjusts a forecast when needed, adds the commentary and marks the snapshot <b>FINAL</b>"],
        ["converts every company to kEUR and builds the group forecast (<b>HQ report</b>, structure of the Excel "
         "“FC CONSO”)",
         "checks completeness, FX, intercompany, minimum cash and liquidity",
         "makes a FINAL snapshot read-only for everybody"], hq=True), unsafe_allow_html=True)

# =========================================================================== intercompany
with t_ic:
    st.markdown("""<div class="gd-card"><h3>You enter what you send – your partner's inflow fills itself</h3>
<div class="gd-ic">
 <div class="gd-co"><div class="t">V009SA · forecast (SAR)</div>
  <div class="r in"><span>IC (VIDE-GMBH)</span><span>to VIDE GmbH</span></div>
  <div class="r in"><span>IC to V004AE</span><span><b>1 000</b></span></div>
  <div class="r calc"><span>IC from … (inflow)</span><span>filled for you</span></div></div>
 <div class="gd-arrow"><b>→</b>after V009SA submits<br>SAR → EUR → AED<br>same week</div>
 <div class="gd-co"><div class="t">V004AE · forecast (AED)</div>
  <div class="r calc"><span>IC from V009SA</span><span><b>≈ 980</b> · read-only</span></div>
  <div class="r in"><span>IC (VIDE-GMBH)</span><span>to VIDE GmbH</span></div>
  <div class="r in"><span>IC to …</span><span>your payments</span></div></div>
</div><p style="color:#64748B;font-size:12.5px;margin:8px 0 0">Illustrative amounts in thousands of local currency.</p></div>""",
                unsafe_allow_html=True)
    st.markdown("""
**The rules (from snapshot 202612)**

| Topic | Rule |
|---|---|
| **What you enter** | Only IC **outflow**, every payment with its receiver: “IC (VIDE-GMBH)” = to VIDE GmbH, “IC to &lt;company&gt;” = to that company. |
| **VIDE GmbH** | Pays the subsidiaries only through rows “IC to &lt;company&gt;”; its own row “IC (VIDE-GMBH)” stays empty. |
| **What you receive** | Calculated – the rows “IC from &lt;company&gt;” (and, for subsidiaries, “IC (VIDE-GMBH)” for VIDE GmbH's payments) cannot be edited. |
| **When it appears** | As soon as the paying company **submits**. Until then its payment is not in your inflow – the editor lists who has not submitted yet. |
| **Currency** | Payer's currency → EUR → your currency, at the FX rates of the snapshot. Without a rate the amount is left out and reported. |
| **Timing** | The same week as the payment. |
| **Not allowed** | IC outflow without a receiver (“IC (other VI-group) – other / not specified”) – it blocks the submit. |
| **Pre-fill** | Companies with exactly one IC partner: amounts on “IC (other VI-group)” are moved to “IC to &lt;partner&gt;” automatically (recorded in the change history). |

**In the group report** intercompany payments are not part of the group's inflow and outflow. Cash balances are
the plain sum of the companies' balances; IC that one side books but the other does not is shown as
**“IC mismatch (not eliminated)”**.
""")

# =========================================================================== validations
with t_val:
    st.markdown("""**Validate & submit** checks your forecast. Red issues block the submit; warnings appear in a
pop-up – go back with **Edit** or submit anyway with a comment (stored with your submission).""")
    VAL = [
        ("b", "Numbers only", "Every value is a number with at most 2 decimal places."),
        ("b", "Opening cash", "The opening cash of the first week is entered."),
        ("b", "Every month filled", "Each forecast month has at least one cash-flow value (0 counts)."),
        ("b", "IC without receiver", "From 202612: every IC outflow names its receiver; VIDE GmbH does not use "
                                     "its row “IC (VIDE-GMBH)”."),
        ("w", "Negative values", "Inflows and outflows are entered as positive numbers (bank financing may be ±)."),
        ("w", "vs previous snapshot", "Month-end cash differs more than ±30 % from last month's forecast."),
        ("w", "vs actuals", "Average month-end cash differs more than ±30 % from the average of the last 6 closed "
                            "months (IDL actuals)."),
        ("w", "Minimum cash", "The lowest weekly cash of a month (in kEUR) is below your minimum cash level −10 %."),
        ("w", "Unnamed line", "A customer / project row has values but no name."),
        ("w", "IC inflow FX", "IC inflow from a partner cannot be converted – FX rate missing."),
        ("i", "Pre-filled month", "The 3rd month is still pre-filled from last month (keeping it is fine)."),
        ("i", "Excess cash", "Cash stays above minimum +20 % in the whole horizon."),
        ("i", "Waiting for partners", "Companies that have not submitted yet – their IC payments to you are not "
                                      "included."),
        ("i", "Company questions", "Fixed reminders, e.g. V004: factoring repayment."),
    ]
    lbl = {"b": "Blocks submit", "w": "Warning", "i": "Information"}
    rows = "".join(f'<tr><td><span class="gd-sev {s}">{lbl[s]}</span></td><td><b>{E(n)}</b></td>'
                   f'<td style="text-align:left;white-space:normal">{E(d)}</td></tr>' for s, n, d in VAL)
    st.markdown(f'<div class="gd-card" style="padding:6px 8px"><table style="width:100%;border-collapse:collapse;'
                f'font-size:13.5px"><thead><tr style="text-align:left;color:#64748B"><th style="padding:8px">Type</th>'
                f'<th style="padding:8px">Check</th><th style="padding:8px">Rule</th></tr></thead><tbody>'
                + rows.replace("<td>", '<td style="padding:8px;border-top:1px solid #EEF2F7">').replace(
                    '<td style="text-align', '<td style="padding:8px;border-top:1px solid #EEF2F7;text-align')
                + "</tbody></table></div>", unsafe_allow_html=True)

# =========================================================================== definitions
with t_def:
    DEFS = [
        ("Snapshot", "One monthly forecast round, named after its first month: 202612 = December 2026, January and "
                     "February 2027."),
        ("Rolling 2+1", "A new snapshot takes over the last forecast: 2 months roll over, the new 3rd month is "
                        "pre-filled from the month before and marked for review."),
        ("Business week (W1–W4)", "Every month has four weeks that do not cross the month end, e.g. October 2026: "
                                  "1–11, 12–18, 19–25, 26–31. Weeks are what you enter; months are calculated."),
        ("Opening cash balance", "Cash at the start of a week. You enter it only for the first week; every later "
                                 "week starts with the previous week's closing."),
        ("Closing cash balance", "Opening + net cash movement of the week. Month value = closing of the last week."),
        ("Net cash movement", "Total cash inflow − total cash outflow + bank financing (borrowing + / repayment −)."),
        ("Detail row", "A row you type into (customer, project, cost type). Subtotals and totals are calculated."),
        ("Draft", "Saved but not submitted – you can still change it."),
        ("Submitted", "Sent to HQ; stored unchangeable and read-only for you."),
        ("Revision", "Every save creates a new numbered version; older versions stay in the history."),
        ("HQ adjustment", "A change HQ makes after you submitted; marked in blue and recorded with a reason."),
        ("FINAL", "HQ has closed the snapshot – read-only for everybody; the group report uses exactly these "
                  "numbers."),
        ("Empty vs 0", "Empty = nothing forecast (counts as 0 in sums but is shown empty); 0 = you expect zero."),
        ("IC outflow / inflow", "Money sent to / received from another VITRONIC company. You enter the outflow; the "
                                "inflow is calculated from your partners' outflow (from 202612)."),
        ("IC mismatch", "IC that one company books but its partner does not (or with a different amount). Shown in "
                        "the group report as its own line."),
        ("FX rate", "IDL rate type SK (BS) of the month before the snapshot: local currency per 1 EUR, one rate for "
                    "all three months."),
        ("kEUR", "Thousand euro – the group report currency: local amount ÷ FX rate ÷ 1 000."),
        ("Minimum cash", "The cash level HQ treasury expects you to keep; below minimum −10 % is a warning, above "
                         "minimum +20 % in all weeks is shown as excess cash."),
    ]
    st.markdown('<div class="gd-card"><div class="gd-def">' + "".join(f"<div>{E(t)}</div><div>{E(d)}</div>"
                                                                      for t, d in DEFS) + "</div></div>",
                unsafe_allow_html=True)

# =========================================================================== methodology
with t_meth:
    st.markdown("""
### Formulas
- `NET CASH MOVEMENT = TOTAL CASH INFLOW − TOTAL CASH OUTFLOW + BANK FINANCING`
- `CLOSING CASH = OPENING CASH + NET CASH MOVEMENT`
- Opening cash of the first week is entered; every later opening equals the previous closing.
- Subtotals and totals are the sums of their detail rows and can never be edited.

| Value | Month column |
|---|---|
| Inflow / outflow / net movement | sum of W1–W4 |
| Opening cash balance | opening of W1 |
| Closing cash balance | closing of W4 |

A calendar for a new month is generated as a proposal and confirmed by HQ.

### Empty is not zero
| State | Meaning | In sums |
|---|---|---|
| Entered | an explicit number, including 0 | the value |
| Blank | optional detail row left empty | 0, shown empty |
| Missing | required input empty (opening cash) | unknown – blocks submit |
| Calculated | derived by the app | – |

### Rolling 2+1
Snapshot *n+1* is created from the latest saved version of snapshot *n*: months 1–2 roll over week by week,
month 3 is copied from the previous month row by row (pre-filled, highlighted for review – a reminder, not a
blocker). The new opening cash equals the calculated closing cash of the preceding week.

### FX and group currency
IDL rate type `SK (BS)` of the month **before** the snapshot, applied to all three months:
`kEUR = local amount / FX rate / 1 000` (FX rate = local currency per 1 EUR). A missing rate is reported –
never replaced; a company without a rate is not consolidated.

### Group consolidation (HQ report)
- Line items = the sum of the same subtotal of all companies, as the Excel “FC CONSO”.
- Intercompany receipts (template rows 64/65) and payments (88/89) are **not** in group inflow / outflow / net.
- **Cash balances = plain sum of the companies' balances** (client decision 2026-11-26). IC that does not net to
  zero stays in the balance as the line “IC mismatch (not eliminated)”: Closing = Opening + Net + IC mismatch.
- Check line “Check (= 0)” = Σ IC inflow − Σ IC outflow, tolerance ±10 kEUR per month.
- Companies marked “no delivery expected” (V013, V014) are listed but not reported as missing.

### Intercompany inflow (from snapshot 202612)
The receiver's IC inflow is calculated from the payers' **submitted** IC outflow (incl. HQ adjustments):
payer currency → EUR → receiver currency at the snapshot FX rates, same week. Until a payer submits, its
payment is not in the receiver's inflow. On submit, the partner versions used are written to the audit trail.

### Process and audit
No approval workflow: companies save drafts and **submit**; the submitted version is stored unchangeable. HQ can
adjust afterwards – each adjustment creates a new version with audit records. Every change keeps who, when, the
old and the new value.

### Data sources
Keboola: the entity Excel templates (parser *3MFC VIT – Excel Parser*), the entity dictionary and prior-month FX
(IDL), the business-week calendar and the pre-fill of the first snapshot. Your inputs are stored in the app's own
tables and are never overwritten by transformations.
""")
