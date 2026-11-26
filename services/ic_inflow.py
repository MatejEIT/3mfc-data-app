"""Derived IC inflow – service glue shared by the Forecast Editor and the HQ report (rules: domain/ic_flow.py)."""
from __future__ import annotations

from dataclasses import dataclass

from domain.ic_flow import DerivedInflow, PayerOutflow, derive_inflow, is_inflow_line, payer_outflow
from domain.ic_partners import parse_partner_marker
from domain.models import InputCell, Line, ValueState
from services.reference_data import ReferenceData

# Companies that are not expected to deliver a forecast (V013, V014) are never 'pending'.
NO_DELIVERY_EXPECTED = frozenset({"V013", "V014"})


def build_payers(ref: ReferenceData, snapshot: str, cells_by_bu: dict[str, dict], revision_by_bu: dict[str, str]
                 ) -> list[PayerOutflow]:
    """PayerOutflow of every SUBMITTED company (cells_by_bu holds only those)."""
    ents = ref.entities()
    all_bus = list(ents["CODE_BUSINESS_UNIT"])
    cur = dict(zip(ents["CODE_BUSINESS_UNIT"], ents["CODE_LOCAL_CURRENCY"]))
    if cells_by_bu:
        ref.prefetch(list(cells_by_bu), snapshot)
    return [payer_outflow(b, revision_by_bu.get(b, ""), str(cur.get(b, "")), ref.fx_rates(b, snapshot), cells, all_bus)
            for b, cells in sorted(cells_by_bu.items())]


def pending_companies(all_bus: list[str], submitted: set[str]) -> list[str]:
    return sorted(b for b in all_bus if b not in submitted and b[:4] not in NO_DELIVERY_EXPECTED)


@dataclass
class Applied:
    lines: list[Line]
    cells: dict[tuple[str, str], InputCell]        # stored inputs without any IC inflow
    labels: dict[str, str]                         # without IC inflow partner markers
    derived: DerivedInflow
    dropped: int                                   # entered IC inflow values no longer used


def apply_derived(bu: str, currency: str, rates: dict, lines: list[Line], cells: dict, labels: dict[str, str],
                  payers: list[PayerOutflow], pending: list[str], names: dict[str, str]) -> Applied:
    """Replace the company's entered IC inflow by the inflow derived from the submitted payers."""
    inflow_ids = {ln.line_id for ln in lines if is_inflow_line(ln)}
    dropped = sum(1 for (lid, _), c in cells.items() if lid in inflow_ids and c.state == ValueState.ENTERED
                  and c.amount)
    kept = {k: c for k, c in cells.items() if k[0] not in inflow_ids}
    labels = {k: v for k, v in labels.items() if (parse_partner_marker(v) or ("",))[0] != "IN"}
    d = derive_inflow(bu, currency, rates, lines, payers, pending, names)
    return Applied(d.lines, kept, labels, d, dropped)
