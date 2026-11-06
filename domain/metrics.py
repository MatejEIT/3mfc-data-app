"""Financial metric dictionary.

Mirrors Keboola table DIM_3MFC_APP_METRIC (32 metrics, verified 2026-11-05)
with clean display labels. `check_dictionary_drift` compares this definition
with the backend table so a template change cannot silently break the app.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from domain.models import RowType


class Aggregation(str, Enum):
    FLOW = "FLOW"                # month = SUM(W1..W4)
    OPENING = "OPENING"          # month = OPENING(W1)
    CLOSING = "CLOSING"          # month = CLOSING(W4)
    MONTH_STATE = "MONTH_STATE"  # one value per month (AR/AP closing balances)


class InputMode(str, Enum):
    LINE_INPUT = "LINE_INPUT"            # weekly detail input
    CALCULATED = "CALCULATED"            # never editable
    OPENING_FIRST = "OPENING_FIRST"      # editable only in first interval of horizon
    MONTH_STATE_INPUT = "MONTH_STATE_INPUT"  # editable once per month


class Section(str, Enum):
    INFLOW = "Cash Inflow"
    OUTFLOW = "Cash Outflow"
    FINANCING = "Financing & Cash Balance"
    BALANCES = "AR / AP Closing Balances"


@dataclass(frozen=True)
class MetricDef:
    code: str
    label: str
    row_type: RowType
    parent: str | None
    template_row: int
    aggregation: Aggregation
    input_mode: InputMode
    section: Section
    is_canonical_kpi: bool = False
    required: bool = False


_D, _S, _T, _B = RowType.DETAIL, RowType.SUBTOTAL, RowType.TOTAL, RowType.BALANCE
_F = Aggregation.FLOW
_LI, _C = InputMode.LINE_INPUT, InputMode.CALCULATED

_METRICS: list[MetricDef] = [
    # ---- Cash inflow -------------------------------------------------------
    MetricDef("AR_NOT_OVERDUE_COLLECTIONS", "AR collections – not overdue", _S, "TOTAL_CASH_INFLOW", 14, _F, _C, Section.INFLOW),
    MetricDef("AR_NOT_OVERDUE_DETAIL", "AR not overdue – detail", _D, "AR_NOT_OVERDUE_COLLECTIONS", 15, _F, _LI, Section.INFLOW),
    MetricDef("AR_OVERDUE_COLLECTIONS", "AR collections – overdue", _S, "TOTAL_CASH_INFLOW", 30, _F, _C, Section.INFLOW),
    MetricDef("AR_OVERDUE_DETAIL", "AR overdue – detail", _D, "AR_OVERDUE_COLLECTIONS", 31, _F, _LI, Section.INFLOW),
    MetricDef("NEW_INVOICE_COLLECTIONS", "New invoice collections", _S, "TOTAL_CASH_INFLOW", 46, _F, _C, Section.INFLOW),
    MetricDef("NEW_INVOICE_DETAIL", "New invoices – detail", _D, "NEW_INVOICE_COLLECTIONS", 47, _F, _LI, Section.INFLOW),
    MetricDef("OTHER_RECEIPTS", "Other receipts", _S, "TOTAL_CASH_INFLOW", 62, _F, _C, Section.INFLOW),
    MetricDef("OTHER_RECEIPTS_DETAIL", "Other receipts – detail", _D, "OTHER_RECEIPTS", 63, _F, _LI, Section.INFLOW),
    MetricDef("TOTAL_CASH_INFLOW", "Total Cash Inflow", _T, None, 66, _F, _C, Section.INFLOW, is_canonical_kpi=True),
    # ---- Cash outflow ------------------------------------------------------
    MetricDef("MATERIAL_EXPENSES", "Material expenses", _D, "TOTAL_MATERIAL_OUTFLOW", 68, _F, _LI, Section.OUTFLOW),
    MetricDef("LOCAL_PURCHASING", "Local purchasing (> 500 kEUR)", _D, "TOTAL_MATERIAL_OUTFLOW", 69, _F, _LI, Section.OUTFLOW),
    MetricDef("PROJECT_SERVICE_EXPENSES", "Project / service expenses", _S, "TOTAL_MATERIAL_OUTFLOW", 70, _F, _C, Section.OUTFLOW),
    MetricDef("PROJECT_EXPENSE_DETAIL", "Project expense – detail", _D, "PROJECT_SERVICE_EXPENSES", 71, _F, _LI, Section.OUTFLOW),
    MetricDef("OTHER_PROJECT_EXPENSES", "Other project expenses (< 500 kEUR)", _D, "TOTAL_MATERIAL_OUTFLOW", 81, _F, _LI, Section.OUTFLOW),
    MetricDef("TOTAL_MATERIAL_OUTFLOW", "Material & project outflow", _S, "TOTAL_CASH_OUTFLOW", 82, _F, _C, Section.OUTFLOW),
    MetricDef("EMPLOYMENT", "Employment", _D, "OTHER_OPERATING_OUTFLOW", 83, _F, _LI, Section.OUTFLOW),
    MetricDef("INVESTMENTS", "Investments", _D, "OTHER_OPERATING_OUTFLOW", 84, _F, _LI, Section.OUTFLOW),
    MetricDef("TAXES", "Taxes", _D, "OTHER_OPERATING_OUTFLOW", 85, _F, _LI, Section.OUTFLOW),
    MetricDef("OTHER_EXPENSES", "Other expenses", _D, "OTHER_OPERATING_OUTFLOW", 86, _F, _LI, Section.OUTFLOW),
    MetricDef("OTHER_OPERATING_OUTFLOW", "Other operating outflow", _S, "TOTAL_CASH_OUTFLOW", 87, _F, _C, Section.OUTFLOW),
    MetricDef("INTERCOMPANY_GMBH_OUTFLOW", "IC outflow – VIDE GmbH", _D, "TOTAL_INTERCOMPANY_OUTFLOW", 88, _F, _LI, Section.OUTFLOW),
    MetricDef("INTERCOMPANY_OTHER_OUTFLOW", "IC outflow – other VI group", _D, "TOTAL_INTERCOMPANY_OUTFLOW", 89, _F, _LI, Section.OUTFLOW),
    MetricDef("TOTAL_INTERCOMPANY_OUTFLOW", "Intercompany outflow", _S, "TOTAL_CASH_OUTFLOW", 90, _F, _C, Section.OUTFLOW),
    MetricDef("TOTAL_CASH_OUTFLOW", "Total Cash Outflow", _T, None, 91, _F, _C, Section.OUTFLOW, is_canonical_kpi=True),
    # ---- Financing & cash balance -----------------------------------------
    MetricDef("BANK_FINANCING_MOVEMENT", "Bank financing (+ borrowing / – repayment)", _D, None, 92, _F, _LI, Section.FINANCING),
    MetricDef("NET_CASH_MOVEMENT", "Net Cash Movement", _T, None, 93, _F, _C, Section.FINANCING, is_canonical_kpi=True),
    MetricDef("OPENING_CASH_BALANCE", "Opening Cash Balance", _B, None, 95, Aggregation.OPENING, InputMode.OPENING_FIRST,
              Section.FINANCING, is_canonical_kpi=True, required=True),
    MetricDef("CLOSING_CASH_BALANCE", "Closing Cash Balance", _B, None, 97, Aggregation.CLOSING, _C,
              Section.FINANCING, is_canonical_kpi=True),
    # ---- AR / AP month-end balances (monthly state, not weekly flows) ------
    MetricDef("AR_CLOSING_BALANCE", "Total receivables (AR closing balance)", _B, None, 99, Aggregation.MONTH_STATE,
              InputMode.MONTH_STATE_INPUT, Section.BALANCES, required=True),
    MetricDef("AP_CLOSING_BALANCE", "Total payables (AP closing balance)", _B, None, 100, Aggregation.MONTH_STATE,
              InputMode.MONTH_STATE_INPUT, Section.BALANCES, required=True),
    MetricDef("AR_OVERDUE_CLOSING_BALANCE", "Total receivables - overdue (AR closing balance)", _B, None, 102,
              Aggregation.MONTH_STATE, InputMode.MONTH_STATE_INPUT, Section.BALANCES, required=True),
    MetricDef("AP_OVERDUE_CLOSING_BALANCE", "Total payables - overdue (AP closing balance)", _B, None, 103,
              Aggregation.MONTH_STATE, InputMode.MONTH_STATE_INPUT, Section.BALANCES, required=True),
]

# Detail lists where the entity types the customer / project name itself (yellow column B in the
# Excel template, rows 15-28, 31-44, 47-60, 71-80). The "OTHER" row of each list is fixed.
NAMEABLE_METRICS: frozenset[str] = frozenset({
    "AR_NOT_OVERDUE_DETAIL", "AR_OVERDUE_DETAIL", "NEW_INVOICE_DETAIL", "PROJECT_EXPENSE_DETAIL",
})
FIXED_LABELS: frozenset[str] = frozenset({"OTHER"})
# Template texts that mean "this slot is still free" (seen in the 16 entity files).
PLACEHOLDER_LABELS: frozenset[str] = frozenset({
    "", "0", "#ref!", "project name and customer name", "name project expense", "project name + project no.",
})
MAX_LABEL_LENGTH = 120


def is_nameable(metric_code: str, base_label: str) -> bool:
    return metric_code in NAMEABLE_METRICS and base_label.strip().upper() not in FIXED_LABELS


def is_placeholder_label(label: str) -> bool:
    return label.strip().lower() in PLACEHOLDER_LABELS

METRICS: dict[str, MetricDef] = {m.code: m for m in _METRICS}
METRIC_ORDER: list[str] = [m.code for m in sorted(_METRICS, key=lambda m: m.template_row)]

AR_AP_METRICS: tuple[str, ...] = (
    "AR_CLOSING_BALANCE", "AP_CLOSING_BALANCE", "AR_OVERDUE_CLOSING_BALANCE", "AP_OVERDUE_CLOSING_BALANCE",
)
IC_SPLIT_METRIC = "INTERCOMPANY_OTHER_OUTFLOW"


def children_of(code: str) -> list[str]:
    return [m.code for m in _METRICS if m.parent == code]


def _depth(code: str) -> int:
    d, cur = 0, METRICS[code].parent
    while cur:
        d, cur = d + 1, METRICS[cur].parent
    return d


# Roll-up order: deepest aggregates first, so parents see finished children.
ROLLUP_ORDER: list[str] = sorted(
    (m.code for m in _METRICS if m.row_type in (RowType.SUBTOTAL, RowType.TOTAL) and m.code != "NET_CASH_MOVEMENT"),
    key=_depth,
    reverse=True,
)


def check_dictionary_drift(backend_rows: list[dict]) -> list[str]:
    """Compare DIM_3MFC_APP_METRIC rows with this definition. Returns problems."""
    problems: list[str] = []
    seen = set()
    for r in backend_rows:
        code = r.get("METRIC_CODE", "")
        seen.add(code)
        m = METRICS.get(code)
        if m is None:
            problems.append(f"Backend metric {code} unknown to the app")
            continue
        if (r.get("PARENT_METRIC_CODE") or None) != m.parent:
            problems.append(f"{code}: parent {r.get('PARENT_METRIC_CODE')!r} != app {m.parent!r}")
        if r.get("ROW_TYPE") != m.row_type.value:
            problems.append(f"{code}: row type {r.get('ROW_TYPE')} != app {m.row_type.value}")
        if int(float(r.get("TEMPLATE_ROW") or 0)) != m.template_row:
            problems.append(f"{code}: template row {r.get('TEMPLATE_ROW')} != app {m.template_row}")
    for code in METRICS:
        if code not in seen:
            problems.append(f"App metric {code} missing in backend dictionary")
    return problems
