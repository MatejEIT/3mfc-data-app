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


# --- partners added by the user in the grid ("＋ add IC partner") ---------------------------
# Stored per revision in the line-label table as a marker, so they are versioned, audited and
# carried over by rolling 2+1 exactly like customer names.
_MARKER = "IC_PARTNER"
OTHER_SUFFIX = " – other / not specified"


def partner_marker(direction: str, partner_bu: str) -> str:
    return f"{_MARKER}|{direction}|{partner_bu}"


def parse_partner_marker(label: str) -> tuple[str, str] | None:
    parts = str(label or "").split("|")
    if len(parts) == 3 and parts[0] == _MARKER and parts[1] in ("IN", "OUT") and parts[2]:
        return parts[1], parts[2]
    return None


def base_direction(line) -> str | None:
    """'IN' / 'OUT' for the template row 'IC (other VI-group)', else None."""
    if getattr(line, "ic_partner", ""):
        return None
    if line.source_row == IC_OTHER_INFLOW_ROW and line.metric_code == IC_OTHER_INFLOW_METRIC:
        return "IN"
    if line.source_row == IC_OTHER_OUTFLOW_ROW and line.metric_code == IC_OTHER_OUTFLOW_METRIC:
        return "OUT"
    return None


def add_partner_rows(business_unit: str, lines: list, partners: dict[str, list[str]], names: dict[str, str]) -> list:
    """Insert one input row per partner under 'IC (other VI-group)'; the template row is kept
    as 'other / not specified' (after the partner rows). Already present partner rows are kept."""
    from dataclasses import replace

    from domain.models import Line, RowType

    present = {ln.line_id for ln in lines}
    count = {d: sum(1 for ln in lines if ln.ic_partner and partner_direction(ln) == d) for d in ("IN", "OUT")}
    out = []
    for ln in lines:
        d = base_direction(ln)
        if d is None:
            out.append(ln)
            continue
        new = [p for p in partners.get(d, []) if p != business_unit
               and partner_line_id(business_unit, d, p) not in present]
        if (new or count[d]) and not ln.label.endswith(OTHER_SUFFIX):
            ln = replace(ln, label=ln.label + OTHER_SUFFIX, order=99)
        out.append(ln)
        arrow = "from" if d == "IN" else "to"
        for i, p in enumerate(new, count[d] + 1):
            out.append(Line(partner_line_id(business_unit, d, p), ln.metric_code, RowType.DETAIL,
                            f"IC {arrow} {p} · {names.get(p, '')}".strip(" ·"), ln.source_row, order=i, ic_partner=p))
            present.add(partner_line_id(business_unit, d, p))
        count[d] += len(new)
    return sorted(out, key=lambda x: (x.source_row, x.order, x.line_id))


def partner_direction(line) -> str:
    return "IN" if line.metric_code == IC_OTHER_INFLOW_METRIC and line.source_row == IC_OTHER_INFLOW_ROW else "OUT"


def with_added_partners(business_unit: str, lines: list, labels: dict[str, str], names: dict[str, str]) -> list:
    """Base lines + partner rows the user added (markers in the revision's line labels)."""
    partners: dict[str, list[str]] = {"IN": [], "OUT": []}
    for line_id, label in labels.items():
        parsed = parse_partner_marker(label)
        if parsed and line_id == partner_line_id(business_unit, *parsed):
            partners[parsed[0]].append(parsed[1])
    if not partners["IN"] and not partners["OUT"]:
        return lines
    return add_partner_rows(business_unit, lines, {d: sorted(ps) for d, ps in partners.items()}, names)
