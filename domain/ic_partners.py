"""Intercompany partner pairs ("other VI-group" IC).

Pairs of companies that have IC cash flows with each other (colour-coded pairs in the HQ
"other IC inflow / outflow" overview). Each company gets, in its forecast grid, one input row
per partner under "IC (other VI-group)" - for inflow (template row 65) AND outflow (row 89).
The original template row stays as "other / not specified" so no amount is lost.

Codes are CODE_ENTITY (V002), mapped to CODE_BUSINESS_UNIT (V002US) via DIM_3MFC_ENTITY.
To change the pairs, edit this list (later it can move to a Keboola table maintained by HQ).
"""
from __future__ import annotations

import hashlib

IC_PARTNER_PAIRS: tuple[tuple[str, str], ...] = (
    ("V002", "V016"),
    ("V005", "V011"),
    ("V004", "V009"),
    ("V007", "V012"),
    ("V008", "V017"),
    ("V008", "V010"),   # V010 IC inflow (user: "probably with V008", 2026-11-10)
)

# template rows of "IC (other VI-group)"
IC_OTHER_INFLOW_ROW = 65      # metric OTHER_RECEIPTS_DETAIL
IC_OTHER_OUTFLOW_ROW = 89     # metric INTERCOMPANY_OTHER_OUTFLOW
IC_OTHER_INFLOW_METRIC = "OTHER_RECEIPTS_DETAIL"
IC_OTHER_OUTFLOW_METRIC = "INTERCOMPANY_OTHER_OUTFLOW"


def partners_of(entity_code: str) -> list[str]:
    """Partner CODE_ENTITYs of a company, in a stable order."""
    out = [b for a, b in IC_PARTNER_PAIRS if a == entity_code] + [a for a, b in IC_PARTNER_PAIRS if b == entity_code]
    return sorted(set(out))


def partner_line_id(business_unit: str, direction: str, partner_bu: str) -> str:
    """Stable LINE_ID of the partner row ('IN' or 'OUT')."""
    return hashlib.sha256(f"{business_unit}|IC_PARTNER|{direction}|{partner_bu}".encode()).hexdigest()
