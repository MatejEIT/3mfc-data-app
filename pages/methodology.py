"""Methodology – the rules the application enforces."""
import streamlit as st

st.markdown("""
### Data sources
Keboola project 810. Monthly and weekly facts come from the original entity Excel templates
(OneDrive `SharedEmbedIT/008_3MFC`, parser *3MFC VIT – Excel Parser*), FX and the entity dictionary
from *Entity Dictionary & Prior-Month FX*, the business-week calendar and Excel seed from *Weekly Cash
Flow Facts & App Prefill*. User inputs are stored in the separate app bucket and are never overwritten
by transformations.

### Weekly business intervals
Every month has four business intervals W1–W4 (not ISO weeks), e.g. October 2026: 1–11, 12–18, 19–25, 26–31.
Weekly values are the **only** editable flow granularity; monthly values are always derived:

| Metric type | Month value |
|---|---|
| Cash inflow / outflow / net movement | sum of W1–W4 |
| Opening cash balance | opening of W1 |
| Closing cash balance | closing of W4 |

A calendar for a new month is generated as a **proposal** (`PROPOSED_CONFIRM_REQUIRED`) and must be confirmed by HQ.

### Formulas
- `NET_CASH_MOVEMENT = TOTAL_CASH_INFLOW − TOTAL_CASH_OUTFLOW + BANK_FINANCING_MOVEMENT`
- `CLOSING_CASH_BALANCE = OPENING_CASH_BALANCE + NET_CASH_MOVEMENT`
- Opening cash of the first interval is an input; every later opening equals the previous closing.
- Subtotals and totals are sums of their detail lines and can never be edited.

### Empty is not zero
| State | Meaning | In sums |
|---|---|---|
| ENTERED | explicit number, including 0 | value |
| BLANK | optional detail line left empty | 0, displayed empty |
| MISSING | required input empty (opening cash) | unknown – blocks SUBMIT |
| CALCULATED | derived by the engine | – |

### Rolling 2+1
Snapshot *n+1* is created from the latest **committed** revision of snapshot *n*: months 1–2 roll over
interval by interval (`ROLLOVER`), month 3 is copied from the previous month slot by slot (`CARRY_FORWARD`)
and must be reviewed explicitly. The new opening cash equals the calculated closing cash of the preceding interval.

### FX
IDL rate type `SK (BS)` of the month **before** the snapshot, applied to all three months.
`EUR = LCY / FX_RATE` (FX_RATE = local currency units per 1 EUR). A missing rate is reported – never replaced.

### Intercompany
Only *IC outflow – other VI group* (template row 89) is split by counterparty; the split must equal the line
in every interval. As in the consolidation workbook, the **official group figures eliminate intercompany flows**
(IC receipts and IC outflows are left out of group inflow / outflow); the non-eliminated view is shown for
reconciliation only.

### Process
No approval workflow: entities save drafts and **SUBMIT**. The submitted revision is stored immutably.
HQ can adjust afterwards; each saved adjustment creates a new revision and audit records and marks the
entity for a summary notification.
""")
