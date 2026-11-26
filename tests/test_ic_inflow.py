"""Derived IC inflow (from snapshot 202612; here switched on for the 202610 test snapshot): a company
enters only its IC outflow, the receiver's IC inflow is calculated from the submitted outflow."""
from decimal import Decimal

import pandas as pd
import pytest

import domain.ic_flow as F
from domain.ic_partners import partner_line_id
from domain.models import Line, RowType, ValueState
from domain.validation import Edit
from services.report_service import ReportService
from tests.conftest import BU, cell, seed_frame

D = Decimal
SEED = ("weekly", "FT_3MFC_APP_WEEKLY_INPUT_SEED")
FILL = [Edit("L_TAXES", f"{p}_W2", "0") for p in ("202610", "202611", "202612")]   # every month has values


@pytest.fixture
def derived(monkeypatch):
    monkeypatch.setattr(F, "IC_DERIVED_FROM", "202610")


@pytest.fixture
def two_companies(gateway, snapshot_service, ref, hq, derived):
    gmbh = seed_frame("V001DE")
    gmbh.loc[gmbh["LINE_ID"] == "L_INTERCOMPANY_OTHER_OUTFLOW", "BALANCE_AMOUNT_LCY"] = ""   # GmbH: no unnamed IC
    gateway.sources[SEED] = pd.concat([gateway.sources[SEED], gmbh], ignore_index=True)
    ref.invalidate()
    snapshot_service.import_initial(hq, "202610", [BU, "V001DE"])


# ------------------------------------------------------------------ domain rules
def test_payer_outflow_routes_row88_to_gmbh_and_partner_rows_to_the_partner():
    cells = {c.key: c for c in [cell("L_INTERCOMPANY_GMBH_OUTFLOW", "202610_W1", 1100),
                                cell("L_INTERCOMPANY_OTHER_OUTFLOW", "202610_W1", 50)]}     # unnamed -> not routed
    p = cell("L_INTERCOMPANY_OTHER_OUTFLOW", "202610_W2", 220)
    p = type(p)(partner_line_id(BU, "OUT", "V016MX"), p.metric_code, p.code_period_value, p.code_week_period,
                p.amount, p.state, p.origin)
    cells[p.key] = p
    out = F.payer_outflow(BU, "R1", "USD", {"202610": D("1.1")}, cells, ["V001DE", BU, "V016MX"])
    assert out.to == {"V001DE": {"202610_W1": D(1100)}, "V016MX": {"202610_W2": D(220)}}


def test_derive_inflow_converts_currency_and_flags_missing_fx_and_pending():
    lines = [Line("R64", "OTHER_RECEIPTS_DETAIL", RowType.DETAIL, "IC (VIDE-GMBH)", 64),
             Line("R65", "OTHER_RECEIPTS_DETAIL", RowType.DETAIL, "IC (other VI-group)", 65),
             Line("O89", "INTERCOMPANY_OTHER_OUTFLOW", RowType.DETAIL, "IC (other VI-group)", 89)]
    us = F.PayerOutflow(BU, "US-R2", "USD", {"202610": D("1.1")}, {"V016MX": {"202610_W1": D(110)}})
    de = F.PayerOutflow("V001DE", "DE-R3", "EUR", {}, {"V016MX": {"202610_W2": D(40)}})
    d = F.derive_inflow("V016MX", "MXN", {"202610": D(20)}, lines, [us, de], ["V009SA"], {BU: "US"})
    ids = {ln.line_id: ln for ln in d.lines}
    us_row = partner_line_id("V016MX", "IN", BU)
    assert us_row in ids and ids[us_row].derived and ids["R64"].derived and not ids["O89"].derived
    assert d.cells[(us_row, "202610_W1")].amount == D("2000.00")        # 110 USD / 1.1 * 20 MXN
    assert d.cells[("R64", "202610_W2")].amount == D(800)                # VIDE GmbH -> row 64
    assert d.pending == ["V009SA"] and not d.fx_missing
    d2 = F.derive_inflow("V016MX", "MXN", {}, lines, [us], [], {})        # receiver without FX
    assert d2.fx_missing == [BU] and not d2.cells


def test_outflow_without_receiver_blocks():
    lines = [Line("O89", "INTERCOMPANY_OTHER_OUTFLOW", RowType.DETAIL, "IC (other VI-group)", 89),
             Line("O88", "INTERCOMPANY_GMBH_OUTFLOW", RowType.DETAIL, "IC (VIDE-GMBH)", 88)]
    from dataclasses import replace
    c = cell("L_INTERCOMPANY_OTHER_OUTFLOW", "202610_W1", 5)
    cells = {("O89", "202610_W1"): replace(c, line_id="O89"),
             ("O88", "202610_W1"): replace(c, line_id="O88", metric_code="INTERCOMPANY_GMBH_OUTFLOW")}
    assert sorted(i.code for i in F.outflow_issues(BU, lines, cells)) == ["E_IC_NO_PARTNER"]   # row 88 fine for a sub
    assert sorted(i.code for i in F.outflow_issues("V001DE", lines, cells)) == ["E_IC_GMBH_ROW88", "E_IC_NO_PARTNER"]


# ------------------------------------------------------------------ editor + report
def test_receiver_gets_the_submitted_outflow_as_read_only_inflow(two_companies, forecast_service, repo, hq):
    # V002US pays VIDE GmbH 1,100 USD (row 88); its unnamed 'other VI-group' 100 moves to its only partner V016MX
    ws = forecast_service.open(hq, "202610", BU)
    target = partner_line_id(BU, "OUT", "V016MX")
    assert ws.state.cells[(target, "202610_W2")].amount == D(100) and ws.pending_audit
    assert "V001DE" in ws.ic_inflow.pending
    gm_before = forecast_service.open(hq, "202610", "V001DE")
    assert not gm_before.derived_cells                                   # V002US has not submitted yet
    out = forecast_service.save(hq, ws, [Edit("L_INTERCOMPANY_GMBH_OUTFLOW", "202610_W1", "1100")] + FILL,
                                None, "IC to GmbH", "s1", submit=True)
    assert out.revision is not None, [i.code for i in out.issues]
    audit = repo.audit("202610", BU)
    assert audit["COMMENT"].str.contains("moved from 'IC \\(other VI-group\\)'").any()
    assert audit["COMMENT"].str.contains("IC inflow from partners").any()

    gm = forecast_service.open(hq, "202610", "V001DE")
    row = partner_line_id("V001DE", "IN", BU)
    line = gm.lines_by_id[row]
    assert line.derived and line.label.startswith("IC from V002US")
    assert gm.derived_cells[(row, "202610_W1")].amount == D(1000)         # 1,100 USD / 1.1 = 1,000 EUR
    assert gm.result.line_weekly[(row, "202610_W1")].amount == D(1000)
    # the inflow is calculated: editing it is refused, nothing of it is stored with GmbH's revision
    bad = forecast_service.save(hq, gm, [Edit(row, "202610_W1", "5")], None, "", "g1")
    assert bad.revision is None and "E_NOT_EDITABLE" in [i.code for i in bad.issues]
    forecast_service.save(hq, gm, FILL, None, "", "g2")
    assert all(k[0] != row for k in repo.load_state("202610", "V001DE").cells)

    rep = ReportService(repo, forecast_service.ref).build("202610")
    assert rep.ic_derived and BU not in rep.pending
    g = next(x for x in rep.companies if x.business_unit == "V001DE").flows
    assert g.weekly["ic_in"]["202610_W1"] == D(1)                        # 1,000 EUR = 1 kEUR
    w1 = rep.conso.weekly["ic_diff"]["202610_W1"]
    assert w1 == 0                                                       # both sides agree by construction


def test_entered_inflow_is_dropped_and_unnamed_outflow_blocks_submit(two_companies, forecast_service, hq):
    ws = forecast_service.open(hq, "202610", BU)
    out = forecast_service.save(hq, ws, [Edit("L_INTERCOMPANY_OTHER_OUTFLOW", "202610_W3", "7")] + FILL, None, "",
                                "x1", submit=True)
    assert out.revision is None and "E_IC_NO_PARTNER" in [i.code for i in out.issues]
    assert all(not ln.derived or ln.metric_code == "OTHER_RECEIPTS_DETAIL" for ln in ws.lines)
    assert ("L_IC_OTHER_IN", "202610_W1") not in ws.state.cells or \
        ws.state.cells[("L_IC_OTHER_IN", "202610_W1")].state != ValueState.ENTERED
