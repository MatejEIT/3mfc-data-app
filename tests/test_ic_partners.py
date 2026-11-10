"""IC 'other VI-group' entered per partner company directly in the grid (inflow and outflow)."""
from decimal import Decimal

from domain.ic_partners import partner_line_id, partners_of
from domain.validation import Edit
from tests.conftest import BU


def test_pairs_are_symmetric():
    assert partners_of("V002") == ["V016"] and partners_of("V016") == ["V002"]
    assert partners_of("V008") == ["V010", "V017"] and partners_of("V010") == ["V008"]
    assert partners_of("V001") == []


def test_partner_rows_for_inflow_and_outflow_follow_the_ic_row(ref):
    lines = ref.lines(BU)
    labels = [ln.label for ln in lines]
    pin, pout = partner_line_id(BU, "IN", "V016MX"), partner_line_id(BU, "OUT", "V016MX")
    ids = [ln.line_id for ln in lines]
    assert pin in ids and pout in ids
    i_in, i_out = ids.index(pin), ids.index(pout)
    assert labels[i_in] == "IC from V016MX · VITRONIC Mexico"
    assert labels[i_out] == "IC to V016MX · VITRONIC Mexico"
    # partner row first, then the template row as "other / not specified" - both in the same block
    assert labels[i_in + 1] == "IC (other VI-group) – other / not specified"
    assert labels[i_out + 1].endswith("– other / not specified")


def test_partner_rows_are_inputs_and_roll_into_the_totals(snapshot_service, forecast_service, repo, hq):
    snapshot_service.import_initial(hq, "202610", [BU])
    ws = forecast_service.open(hq, "202610", BU)
    before_in = ws.result.metric_weekly[("OTHER_RECEIPTS", "202610_W1")].amount
    before_out = ws.result.metric_weekly[("TOTAL_INTERCOMPANY_OUTFLOW", "202610_W1")].amount
    out = forecast_service.save(hq, ws, [Edit(partner_line_id(BU, "IN", "V016MX"), "202610_W1", "556"),
                                         Edit(partner_line_id(BU, "OUT", "V016MX"), "202610_W1", "122")],
                                None, "IC with Mexico", "ic1")
    assert out.revision is not None
    ws2 = forecast_service.open(hq, "202610", BU)
    assert ws2.result.metric_weekly[("OTHER_RECEIPTS", "202610_W1")].amount == before_in + Decimal(556)
    assert ws2.result.metric_weekly[("TOTAL_INTERCOMPANY_OUTFLOW", "202610_W1")].amount == before_out + Decimal(122)
    assert not any(i.code == "B_IC_UNALLOCATED" for i in ws2.issues)


def test_company_without_partner_has_no_extra_rows(ref):
    assert not any(ln.ic_partner for ln in ref.lines("V001DE"))
