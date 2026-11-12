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


def test_user_can_add_an_ic_partner_row_and_it_rolls_over(snapshot_service, forecast_service, repo, hq):
    from domain.ic_partners import partner_marker
    snapshot_service.import_initial(hq, "202610", [BU])
    ws = forecast_service.open(hq, "202610", BU)
    new_line = partner_line_id(BU, "OUT", "V001DE")
    assert new_line not in ws.lines_by_id
    out = forecast_service.save(hq, ws, [Edit(new_line, "202612_W2", "75")], None, "new IC partner", "add1",
                                label_edits={new_line: partner_marker("OUT", "V001DE")})
    assert out.revision is not None, [i.message for i in out.issues]
    ws2 = forecast_service.open(hq, "202610", BU)
    line = ws2.lines_by_id[new_line]
    assert ws2.label(line) == "IC to V001DE · VITRONIC Machine Vision GmbH"
    assert ws2.result.line_weekly[(new_line, "202612_W2")].amount == Decimal(75)
    audit = repo.audit("202610", BU)
    assert audit["COMMENT"].str.contains("IC partner added").any()

    snapshot_service.create_rollover(hq, "202611", [BU])
    nov = forecast_service.open(hq, "202611", BU)
    assert new_line in nov.lines_by_id
    assert nov.state.cells[(new_line, "202612_W2")].amount == Decimal(75)


def test_invalid_ic_partner_is_rejected(snapshot_service, forecast_service, hq):
    from domain.ic_partners import partner_marker
    snapshot_service.import_initial(hq, "202610", [BU])
    ws = forecast_service.open(hq, "202610", BU)
    own = partner_line_id(BU, "IN", BU)
    out = forecast_service.save(hq, ws, [], None, "", "bad", label_edits={own: partner_marker("IN", BU)})
    assert out.revision is None and out.issues[0].code == "E_IC_PARTNER"


def test_added_ic_partner_can_be_removed(snapshot_service, forecast_service, repo, hq):
    from domain.ic_partners import partner_marker
    snapshot_service.import_initial(hq, "202610", [BU])
    ws = forecast_service.open(hq, "202610", BU)
    new_line = partner_line_id(BU, "IN", "V001DE")
    forecast_service.save(hq, ws, [Edit(new_line, "202610_W1", "9")], None, "", "a1",
                          label_edits={new_line: partner_marker("IN", "V001DE")})
    ws = forecast_service.open(hq, "202610", BU)
    assert new_line in ws.lines_by_id
    out = forecast_service.save(hq, ws, [], None, "not needed", "r1", label_edits={new_line: ""})
    assert out.revision is not None, [i.message for i in out.issues]
    ws = forecast_service.open(hq, "202610", BU)
    assert new_line not in ws.lines_by_id
    assert repo.audit("202610", BU)["COMMENT"].str.contains("IC partner removed").any()
